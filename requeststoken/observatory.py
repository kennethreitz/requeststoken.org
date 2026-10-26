"""Download snapshots from ClickHouse, with a shared Redis cache and lease.

This is a delayed daily source. Realtime event collection is intentionally not
implemented. Requests return the last snapshot while one background read runs.
"""

from __future__ import annotations

import copy
import orjson
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from threading import RLock
from uuid import uuid4

import httpx

from requeststoken.config import ROOT, USER_AGENT
from requeststoken.data import SystemClock
from requeststoken.observatory_store import DEFAULT_PREFIX, RedisSnapshotStore

logger = logging.getLogger("requeststoken.observatory")
ASSETS = ROOT / "static" / "observatory"
ENDPOINT = "https://sql-clickhouse.clickhouse.com/"
SETTINGS = (
    "SETTINGS max_execution_time=25, read_overflow_mode='throw', "
    "timeout_overflow_mode='throw', result_overflow_mode='throw', "
    "group_by_overflow_mode='throw' FORMAT JSON"
)
MAX_SAFE_INTEGER = 2**53 - 1


def iso_day(value: str) -> str:
    if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
        raise ValueError("Invalid calendar date")
    return value


def count(value) -> int:
    if isinstance(value, str) and value.isascii() and value.isdigit():
        value = int(value)
    if type(value) is not int or not 0 <= value <= MAX_SAFE_INTEGER:
        raise ValueError("Invalid download count")
    return value


def queries_for(today: str, latest: str, first: str = "2016-01-22") -> dict:
    today, latest, first = map(iso_day, (today, latest, first))
    cutoff = (date.fromisoformat(latest) + timedelta(days=1)).isoformat()
    return {
        "daily": "SELECT date, sum(count) AS downloads FROM pypi.pypi_downloads_per_day "
        f"WHERE project='requests' AND date<='{today}' GROUP BY date ORDER BY date {SETTINGS}",
        "total": "SELECT count() AS downloads FROM pypi.pypi WHERE project='requests' "
        f"AND date>='{first}' AND date<'{cutoff}' {SETTINGS}",
        "latest": "SELECT count() AS downloads FROM pypi.pypi WHERE project='requests' "
        f"AND date='{latest}' {SETTINGS}",
    }


def validate_snapshot(value: dict) -> dict:
    if not isinstance(value, dict) or value.get("package") != "requests":
        raise ValueError("Invalid package snapshot")
    first, latest = iso_day(value["firstDate"]), iso_day(value["latestDate"])
    raw_total, raw_latest = count(value["rawTotal"]), count(value["rawLatest"])
    if first > latest or raw_total < raw_latest:
        raise ValueError("Invalid snapshot range")
    stamp = datetime.fromisoformat(value["fetchedAt"].replace("Z", "+00:00"))
    if stamp.tzinfo is None:
        raise ValueError("Snapshot timestamp must include a timezone")
    rows = value["daily"]
    if not isinstance(rows, list) or not 1 <= len(rows) <= 10_000:
        raise ValueError("Invalid daily history")
    daily, previous = [], ""
    for row in rows:
        day = iso_day(row["date"])
        if not first <= day <= latest or day <= previous:
            raise ValueError("Unsorted, duplicate or out-of-range date")
        daily.append({"date": day, "downloads": count(row["downloads"])})
        previous = day
    if daily[0]["date"] != first or daily[-1]["date"] != latest:
        raise ValueError("Snapshot bounds disagree with its history")
    return {
        "package": "requests", "firstDate": first, "latestDate": latest,
        "rawTotal": raw_total, "rawLatest": raw_latest,
        "fetchedAt": value["fetchedAt"], "daily": daily,
        "source": "ClickPy / ClickHouse · PyPI download logs",
        "queries": queries_for(latest, latest, first),
    }


@lru_cache(maxsize=1)
def adoption_manifest() -> dict:
    return orjson.loads((ASSETS / "adoption-evidence.json").read_text(encoding="utf-8"))


class DownloadObservatory:
    def __init__(self, *, store=None, seed=None, client=None, clock=None,
                 refresh_enabled=None, executor=None, env=None):
        env = os.environ if env is None else env
        self.seed = validate_snapshot(seed if seed is not None else orjson.loads(
            (ASSETS / "download-snapshot.json").read_text(encoding="utf-8")
        ))
        self.clock = clock or SystemClock()
        self.client = client
        self.refresh_enabled = (
            env.get("REQUESTS_OBSERVATORY_REFRESH", "1") != "0"
            if refresh_enabled is None else refresh_enabled
        )
        self.executor = executor or ThreadPoolExecutor(max_workers=1, thread_name_prefix="downloads")
        self.store = store
        if self.store is None and env.get("REDIS_URL"):
            try:
                self.store = RedisSnapshotStore.from_url(
                    env["REDIS_URL"], env.get("REQUESTS_OBSERVATORY_REDIS_PREFIX", DEFAULT_PREFIX),
                )
            except Exception as exc:
                logger.warning("snapshot store configuration unavailable (%s)", type(exc).__name__)
        self._state_lock = RLock()
        self._last = {"snapshot": self.seed, "checkedAt": self.seed["fetchedAt"], "status": "fallback"}

    @staticmethod
    def _decode(payload):
        value = orjson.loads(payload)
        snapshot = validate_snapshot(value["snapshot"])
        checked = datetime.fromisoformat(value["checkedAt"].replace("Z", "+00:00"))
        if checked.tzinfo is None or value["status"] not in ("current", "cached", "fallback"):
            raise ValueError("Invalid stored snapshot metadata")
        return {"snapshot": snapshot, "checkedAt": value["checkedAt"], "status": value["status"]}

    def read(self) -> dict:
        with self._state_lock:
            envelope = self._last
            status, refreshing = "fallback", False
            if self.store is not None:
                token = uuid4().hex
                try:
                    payload, acquired, refreshing = self.store.read_and_claim(token, self.refresh_enabled)
                    if payload:
                        try:
                            envelope = self._decode(payload)
                            self._last = envelope
                            status = envelope["status"]
                        except Exception as exc:
                            # A fresh, validated source read can repair malformed
                            # cached JSON; it must never be sent to the browser.
                            logger.warning("invalid shared snapshot (%s)", type(exc).__name__)
                    if acquired:
                        try:
                            self.executor.submit(self._refresh, envelope["snapshot"], token)
                        except Exception as exc:
                            logger.warning("snapshot refresh could not start (%s)", type(exc).__name__)
                            recorded = self._record_failure(envelope["snapshot"], token)
                            envelope = self._last
                            status, refreshing = envelope["status"] if recorded else "fallback", False
                except Exception as exc:
                    logger.warning("snapshot cache unavailable (%s)", type(exc).__name__)
                    status, refreshing = "fallback", False
            result = copy.deepcopy(envelope)
        result["status"] = status
        message = None
        if status == "cached":
            message = "The latest source check failed. Showing the last successful snapshot."
        elif status == "fallback":
            message = "Showing a saved snapshot. A fresh source check has not been confirmed."
        result.update(serverTime=self.clock.now().astimezone(timezone.utc).isoformat(),
                      refreshing=refreshing, message=message)
        return result

    def _query(self, sql: str, client) -> list:
        response = client.get(ENDPOINT, params={"user": "demo", "query": sql})
        response.raise_for_status()
        body = response.json()
        if not isinstance(body, dict) or body.get("exception") or not isinstance(body.get("data"), list):
            raise ValueError("Invalid source response")
        rows = body["data"]
        if body.get("rows") != len(rows) or body.get("rows_before_limit_at_least", 0) > len(rows):
            raise ValueError("Truncated source response")
        return rows

    def fetch_snapshot(self, previous: dict) -> dict:
        transport = nullcontext(self.client) if self.client is not None else httpx.Client(
            timeout=httpx.Timeout(30.0, connect=5.0),
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        )
        with transport as client:
            return self._fetch_snapshot(previous, client)

    def _fetch_snapshot(self, previous: dict, client) -> dict:
        today = self.clock.now().astimezone(timezone.utc).date().isoformat()
        rows = self._query(queries_for(today, previous["latestDate"])["daily"], client)
        if not rows or len(rows) < len(previous["daily"]) * .95 or len(rows) > 10_000:
            raise ValueError("Missing source history")
        daily = [{"date": iso_day(r["date"]), "downloads": count(r["downloads"])} for r in rows]
        first, latest = daily[0]["date"], daily[-1]["date"]
        if latest > today or latest < previous["latestDate"]:
            raise ValueError("Regressed or future source date")
        queries = queries_for(today, latest, first)
        total, last = self._query(queries["total"], client), self._query(queries["latest"], client)
        if len(total) != 1 or len(last) != 1:
            raise ValueError("Missing raw counts")
        result = validate_snapshot({
            "package": "requests", "firstDate": first, "latestDate": latest,
            "rawTotal": count(total[0]["downloads"]), "rawLatest": count(last[0]["downloads"]),
            "daily": daily, "fetchedAt": self.clock.now().astimezone(timezone.utc).isoformat(),
        })
        result["queries"] = queries
        return result

    def _refresh(self, previous: dict, token: str):
        try:
            fresh = self.fetch_snapshot(previous)
            envelope = {"snapshot": fresh, "checkedAt": fresh["fetchedAt"], "status": "current"}
            check = {
                "day": self.clock.now().astimezone(timezone.utc).date().isoformat(),
                "fetchedAt": fresh["fetchedAt"], "latestDate": fresh["latestDate"],
                "rawTotal": fresh["rawTotal"], "rawLatest": fresh["rawLatest"],
                "reportedDays": len(fresh["daily"]),
            }
            with self._state_lock:
                if self.store.publish(token, envelope, check=check):
                    self._last = envelope
        except Exception as exc:
            logger.warning("download source refresh failed (%s)", type(exc).__name__)
            self._record_failure(previous, token)

    def _record_failure(self, previous, token):
        envelope = {"snapshot": previous, "checkedAt": self.clock.now().astimezone(timezone.utc).isoformat(),
                    "status": "cached"}
        try:
            with self._state_lock:
                if self.store.publish(token, envelope):
                    self._last = envelope
                    return True
        except Exception as exc:
            logger.warning("could not record source failure (%s)", type(exc).__name__)
        return False
