"""Upstream reads for DexScreener and pypistats.org.

The HTTP client is injectable so tests never touch the network. Successful
payloads are cached in memory and, unless disabled, mirrored to a JSON file
so a later failed read can still show the last good figures.
"""

from __future__ import annotations

import copy
import orjson
import logging
import os
import re
import threading
from datetime import date, datetime, timedelta, timezone

import httpx

from requeststoken.config import (
    CONTRACT,
    DEX_API,
    DEX_PAGE,
    DISK_CACHE,
    PACKAGE,
    PYPI_OVERALL,
    PYPI_RECENT,
    PYPISTATS_PAGE,
    STATS_ERROR_TTL,
    STATS_TTL,
    TOKEN_ERROR_TTL,
    TOKEN_TTL,
    USER_AGENT,
)

logger = logging.getLogger("requeststoken")

EM = "—"
_DECIMAL = re.compile(r"\d+(\.\d+)?")
_MAX_SERIES_DAYS = 210

_DEX_NAMES = {
    "pumpswap": "PumpSwap",
    "raydium": "Raydium",
    "orca": "Orca",
    "meteora": "Meteora",
}


class UpstreamError(Exception):
    """A controlled failure whose message is safe to show on the page."""


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(timezone.utc)

    def timestamp(self) -> float:
        return self.now().timestamp()


class ManualClock:
    """Test clock. Advance it to expire a cache entry without sleeping."""

    def __init__(self, moment: datetime | None = None) -> None:
        self._moment = moment or datetime(2026, 10, 23, 12, 0, tzinfo=timezone.utc)

    def now(self) -> datetime:
        return self._moment

    def timestamp(self) -> float:
        return self._moment.timestamp()

    def advance(self, seconds: float) -> None:
        self._moment += timedelta(seconds=seconds)


class _Entry:
    def __init__(self, value: dict, fresh_until: float, retry_after: float = 0.0) -> None:
        self.value = value
        self.fresh_until = fresh_until
        self.retry_after = retry_after


class TtlCache:
    def __init__(self) -> None:
        self._items: dict[str, _Entry] = {}
        self._lock = threading.Lock()

    def fresh(self, key: str, now: float) -> dict | None:
        with self._lock:
            entry = self._items.get(key)
            if entry and now < entry.fresh_until:
                return copy.deepcopy(entry.value)
            return None

    def ready(self, key: str, now: float) -> bool:
        with self._lock:
            entry = self._items.get(key)
            if entry is None:
                return True
            return now >= entry.retry_after

    def peek(self, key: str) -> dict | None:
        with self._lock:
            entry = self._items.get(key)
            if entry is None:
                return None
            return copy.deepcopy(entry.value)

    def hold(self, key: str, now: float, backoff: float) -> None:
        with self._lock:
            entry = self._items.get(key)
            if entry is not None:
                entry.retry_after = now + backoff

    def store(self, key: str, value: dict, now: float, ttl: float) -> None:
        with self._lock:
            self._items[key] = _Entry(copy.deepcopy(value), fresh_until=now + ttl, retry_after=0.0)

    def remember_expired(self, key: str, value: dict) -> None:
        """Keep a disk snapshot that the next call should try to replace."""
        with self._lock:
            self._items[key] = _Entry(copy.deepcopy(value), fresh_until=0.0, retry_after=0.0)


def format_count(value) -> str:
    if value is None:
        return EM
    return f"{int(value):,}"


def format_usd(value, *, decimals: int = 0) -> str:
    if value is None:
        return EM
    number = float(value)
    sign = "−" if number < 0 else ""
    absolute = abs(number)
    if absolute >= 1_000_000_000:
        return f"{sign}${absolute / 1_000_000_000:.2f}B"
    if absolute >= 1_000_000:
        return f"{sign}${absolute / 1_000_000:.2f}M"
    if decimals:
        return f"{sign}${absolute:,.{decimals}f}"
    return f"{sign}${absolute:,.0f}"


def format_price(value) -> str:
    if value is None or value == "":
        return EM
    text = str(value).strip()
    try:
        number = float(text)
    except ValueError:
        return EM
    if number == 0:
        return "$0"
    sign = "−" if number < 0 else ""
    magnitude = text[1:] if text.startswith("-") else text
    if abs(number) >= 1 or not _DECIMAL.fullmatch(magnitude):
        digits = 4 if abs(number) >= 1 else 8
        rendered = f"{abs(number):,.{digits}f}".rstrip("0").rstrip(".")
        return f"{sign}${rendered}"
    if "." in magnitude:
        magnitude = magnitude.rstrip("0").rstrip(".")
    return f"{sign}${magnitude}"


def format_pct(value) -> str:
    if value is None:
        return EM
    number = float(value)
    body = f"{abs(number):.2f}%"
    if number > 0:
        return f"+{body}"
    if number < 0:
        return f"−{body}"
    return body


def direction(value) -> str:
    if value is None:
        return "flat"
    if value > 0:
        return "up"
    if value < 0:
        return "down"
    return "flat"


def format_stamp(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%d %b %Y, %H:%M:%S UTC")


def pretty_day(iso: str) -> str:
    return datetime.strptime(iso, "%Y-%m-%d").strftime("%d %b %Y")


def _float_or_none(value):
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int_or_none(value):
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _https(value, fallback: str) -> str:
    if isinstance(value, str) and value.startswith("https://"):
        return value
    return fallback


def humanize_dex(dex_id) -> str | None:
    if not dex_id:
        return None
    return _DEX_NAMES.get(str(dex_id).lower(), str(dex_id))


def humanize_chain(chain_id) -> str | None:
    if not chain_id:
        return None
    names = {"solana": "Solana"}
    return names.get(str(chain_id).lower(), str(chain_id))


def activity_display(buys, sells) -> str:
    if buys is None and sells is None:
        return EM
    return f"{format_count(buys)} / {format_count(sells)}"


def window_sum(series: list[dict], days: int) -> tuple[int, str, int]:
    end = date.fromisoformat(series[-1]["date"])
    start = end - timedelta(days=days - 1)
    chosen = [item for item in series if date.fromisoformat(item["date"]) >= start]
    total = sum(int(item["downloads"]) for item in chosen)
    return total, end.isoformat(), len(chosen)


def _window(label: str, total: int, end: str, observations: int, expected: int) -> dict:
    if observations < expected:
        note = f"{observations} days of data, ending {pretty_day(end)}"
    else:
        note = f"Ending {pretty_day(end)}"
    return {
        "label": label,
        "downloads": total,
        "display": format_count(total),
        "note": note,
    }


def series_caption(series: list[dict], source_label: str) -> str:
    if not series:
        return ""
    start = pretty_day(series[0]["date"])
    end = pretty_day(series[-1]["date"])
    latest = format_count(series[-1]["downloads"])
    return (
        f"Daily downloads from {start} to {end}. "
        f"The latest day shown, {end}, was {latest}. Source: {source_label}. "
        f"The vertical scale starts at zero."
    )


def _clip_series(series: list[dict]) -> list[dict]:
    series = sorted(series, key=lambda item: item["date"])
    return series[-_MAX_SERIES_DAYS:]


def stats_status(body: dict) -> str:
    if not body.get("available"):
        return body.get("detail") or "Download stats are temporarily unavailable."
    parts = []
    if body.get("source_label"):
        parts.append(body["source_label"])
    if body.get("fetched_at_display"):
        parts.append(f"as of {body['fetched_at_display']}")
    if body.get("stale"):
        parts.append("last saved figures")
    return " · ".join(parts)


def token_status(body: dict) -> str:
    if not body.get("available"):
        return body.get("detail") or "Market data is temporarily unavailable."
    parts = ["DexScreener"]
    if body.get("dex_display") and body.get("chain_display"):
        parts.append(f"{body['dex_display']} on {body['chain_display']}")
    if body.get("fetched_at_display"):
        parts.append(f"as of {body['fetched_at_display']}")
    if body.get("stale"):
        parts.append("a newer read failed, so these are the last saved figures")
    else:
        parts.append("refreshed about every 30 seconds")
    return " · ".join(parts)


def _stamp_fields(moment: datetime) -> dict:
    return {
        "fetched_at": moment.astimezone(timezone.utc).isoformat(),
        "fetched_at_display": format_stamp(moment),
    }


def build_stats(
    *,
    source: str,
    source_label: str,
    source_url: str,
    total_downloads: int | None,
    windows: list[dict],
    series: list[dict],
    fetched_at: datetime,
    detail: str | None,
    stale: bool = False,
) -> dict:
    figures = []
    if total_downloads is not None:
        figures.append(
            {
                "label": "All-time",
                "downloads": total_downloads,
                "display": format_count(total_downloads),
                "note": "Every counted download of the package",
            }
        )
    figures.extend(windows)
    body = {
        "available": True,
        "package": PACKAGE,
        "source": source,
        "source_label": source_label,
        "source_url": source_url,
        "stale": stale,
        "total_downloads": total_downloads,
        "figures": figures,
        "series": series,
        "series_caption": series_caption(series, source_label),
        "detail": detail,
        **_stamp_fields(fetched_at),
    }
    body["status_display"] = stats_status(body)
    return body


def unavailable_stats(detail: str) -> dict:
    return {
        "available": False,
        "package": PACKAGE,
        "source": "unavailable",
        "source_label": None,
        "source_url": "https://pypistats.org/packages/requests",
        "stale": False,
        "fetched_at": None,
        "fetched_at_display": None,
        "total_downloads": None,
        "figures": [],
        "series": [],
        "series_caption": "",
        "detail": detail,
        "status_display": detail,
    }


def unavailable_token(detail: str) -> dict:
    body = {
        "available": False,
        "source": "dexscreener",
        "source_label": "DexScreener",
        "stale": False,
        "fetched_at": None,
        "fetched_at_display": None,
        "name": PACKAGE,
        "symbol": PACKAGE,
        "contract": CONTRACT,
        "chain": None,
        "chain_display": None,
        "dex": None,
        "dex_display": None,
        "price_usd": None,
        "price_display": EM,
        "price_native": None,
        "price_native_symbol": "SOL",
        "price_native_display": EM,
        "market_cap_usd": None,
        "market_cap_display": EM,
        "change_h24_pct": None,
        "change_display": EM,
        "change_direction": "flat",
        "liquidity_usd": None,
        "liquidity_display": EM,
        "volume_h24_usd": None,
        "volume_display": EM,
        "buys_h24": None,
        "sells_h24": None,
        "buys_display": EM,
        "sells_display": EM,
        "activity_display": EM,
        "pair_url": DEX_PAGE,
        "pair_address": None,
        "listed_on": None,
        "detail": detail,
    }
    body["status_display"] = detail
    return body


def mark_token_stale(body: dict) -> dict:
    updated = copy.deepcopy(body)
    updated["stale"] = True
    updated["detail"] = "A newer read from DexScreener failed. These are the last saved figures."
    updated["status_display"] = token_status(updated)
    return updated


def mark_stats_stale(body: dict) -> dict:
    updated = copy.deepcopy(body)
    updated["stale"] = True
    note = "Showing the last saved download figures because a fresh read failed."
    prior = body.get("detail")
    updated["detail"] = f"{note} {prior}" if prior else note
    updated["status_display"] = stats_status(updated)
    return updated


def _listed_on(value) -> str | None:
    if value is None or value == "":
        return None
    try:
        moment = datetime.fromtimestamp(int(value) / 1000, tz=timezone.utc)
    except (TypeError, ValueError, OSError, OverflowError):
        return None
    return moment.strftime("%d %b %Y")


def normalize_pair(pair: dict, fetched_at: datetime) -> dict:
    if not isinstance(pair, dict):
        raise UpstreamError("DexScreener returned no pairs for this token.")
    base = pair.get("baseToken") or {}
    quote = pair.get("quoteToken") or {}
    address = base.get("address")
    if address and address != CONTRACT:
        raise UpstreamError("DexScreener's first pair does not match this contract.")
    txns = (pair.get("txns") or {}).get("h24") or {}
    volume = pair.get("volume") or {}
    change = pair.get("priceChange") or {}
    liquidity = pair.get("liquidity") or {}
    price = pair.get("priceUsd")
    native = pair.get("priceNative")
    native_symbol = quote.get("symbol") or "SOL"
    change_h24 = _float_or_none(change.get("h24"))
    market_cap = _float_or_none(pair.get("marketCap"))
    liquidity_usd = _float_or_none(liquidity.get("usd"))
    volume_h24 = _float_or_none(volume.get("h24"))
    buys = _int_or_none(txns.get("buys"))
    sells = _int_or_none(txns.get("sells"))
    native_display = EM
    if native not in (None, ""):
        native_display = f"{format_price(native).lstrip('$')} {native_symbol}"
        if native_display.startswith("−"):
            native_display = native_display
    body = {
        "available": True,
        "source": "dexscreener",
        "source_label": "DexScreener",
        "stale": False,
        "name": base.get("name") or PACKAGE,
        "symbol": base.get("symbol") or PACKAGE,
        "contract": CONTRACT,
        "chain": pair.get("chainId"),
        "chain_display": humanize_chain(pair.get("chainId")),
        "dex": pair.get("dexId"),
        "dex_display": humanize_dex(pair.get("dexId")),
        "price_usd": None if price in (None, "") else str(price),
        "price_display": format_price(price),
        "price_native": None if native in (None, "") else str(native),
        "price_native_symbol": native_symbol,
        "price_native_display": native_display,
        "market_cap_usd": market_cap,
        "market_cap_display": format_usd(market_cap),
        "change_h24_pct": change_h24,
        "change_display": format_pct(change_h24),
        "change_direction": direction(change_h24),
        "liquidity_usd": liquidity_usd,
        "liquidity_display": format_usd(liquidity_usd, decimals=2),
        "volume_h24_usd": volume_h24,
        "volume_display": format_usd(volume_h24, decimals=2),
        "buys_h24": buys,
        "sells_h24": sells,
        "buys_display": format_count(buys),
        "sells_display": format_count(sells),
        "activity_display": activity_display(buys, sells),
        "pair_url": _https(pair.get("url"), DEX_PAGE),
        "pair_address": pair.get("pairAddress"),
        "listed_on": _listed_on(pair.get("pairCreatedAt")),
        "detail": None,
        **_stamp_fields(fetched_at),
    }
    body["status_display"] = token_status(body)
    return body


def normalize_pypistats(recent: dict, overall: dict, fetched_at: datetime) -> dict:
    data = recent.get("data") if isinstance(recent, dict) else None
    if not isinstance(data, dict):
        raise UpstreamError("pypistats.org did not include recent downloads.")
    last_week = _int_or_none(data.get("last_week"))
    last_month = _int_or_none(data.get("last_month"))
    if last_week is None or last_month is None:
        raise UpstreamError("pypistats.org did not include recent downloads.")
    rows = overall.get("data") if isinstance(overall, dict) else None
    if not isinstance(rows, list):
        raise UpstreamError("pypistats.org did not include a daily series.")
    series = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        if row.get("category") not in (None, "without_mirrors"):
            continue
        day = row.get("date")
        count = _int_or_none(row.get("downloads"))
        if not day or count is None:
            continue
        series.append({"date": str(day), "downloads": count})
    series = _clip_series(series)
    if not series:
        raise UpstreamError("pypistats.org did not include a daily series.")
    return build_stats(
        source="pypistats",
        source_label="pypistats.org",
        source_url=PYPISTATS_PAGE,
        total_downloads=None,
        windows=[
            {
                "label": "Last week",
                "downloads": last_week,
                "display": format_count(last_week),
                "note": "As reported by pypistats",
            },
            {
                "label": "Last month",
                "downloads": last_month,
                "display": format_count(last_month),
                "note": "As reported by pypistats",
            },
        ],
        series=series,
        fetched_at=fetched_at,
        detail=None,
    )


def default_client() -> httpx.Client:
    return httpx.Client(
        timeout=httpx.Timeout(6.0, connect=3.0),
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        follow_redirects=True,
    )


class Services:
    def __init__(self, client=None, cache=None, clock=None, env=None, disk=DISK_CACHE) -> None:
        self.client = client or default_client()
        self.cache = cache or TtlCache()
        self.clock = clock or SystemClock()
        self.env = os.environ if env is None else env
        self.disk = disk
        self._token_lock = threading.Lock()
        self._stats_lock = threading.Lock()
        self._disk_lock = threading.Lock()
        self._load_disk()

    def token(self) -> dict:
        with self._token_lock:
            return self._token_locked()

    def stats(self) -> dict:
        with self._stats_lock:
            return self._stats_locked()

    def _token_locked(self) -> dict:
        now = self.clock.timestamp()
        fresh = self.cache.fresh("token", now)
        if fresh is not None:
            return fresh
        if not self.cache.ready("token", now):
            held = self._held("token")
            if held is not None:
                return held
        try:
            body = self._load_token()
        except UpstreamError as exc:
            logger.warning("dexscreener: %s", exc)
            return self._fail_token(str(exc), now)
        except Exception as exc:
            self._log_failure("dexscreener", exc)
            return self._fail_token("Market data is temporarily unavailable.", now)
        self._remember("token", body, now, TOKEN_TTL)
        return body

    def _stats_locked(self) -> dict:
        now = self.clock.timestamp()
        fresh = self.cache.fresh("stats", now)
        if fresh is not None:
            return fresh
        if not self.cache.ready("stats", now):
            held = self._held("stats")
            if held is not None:
                return held
        try:
            recent = self._get_json(PYPI_RECENT)
            overall = self._get_json(PYPI_OVERALL, params={"mirrors": "false"})
            body = normalize_pypistats(recent, overall, self.clock.now())
            body["detail"] = (
                "Counts are from pypistats.org, the public PyPI download statistics, "
                "with mirror traffic excluded."
            )
            body["status_display"] = stats_status(body)
            self._remember("stats", body, now, STATS_TTL)
            return body
        except Exception as exc:
            self._log_failure("pypistats", exc)
            return self._fail_stats("Download stats are temporarily unavailable.", now)

    def _held(self, key: str) -> dict | None:
        held = self.cache.peek(key)
        if held is None:
            return None
        if held.get("available"):
            return mark_token_stale(held) if key == "token" else mark_stats_stale(held)
        return held

    def _fail_token(self, message: str, now: float) -> dict:
        held = self.cache.peek("token")
        if held and held.get("available"):
            self.cache.hold("token", now, TOKEN_ERROR_TTL)
            return mark_token_stale(held)
        failure = unavailable_token(message)
        self.cache.store("token", failure, now, TOKEN_ERROR_TTL)
        return failure

    def _fail_stats(self, message: str, now: float) -> dict:
        held = self.cache.peek("stats")
        if held and held.get("available"):
            self.cache.hold("stats", now, STATS_ERROR_TTL)
            return mark_stats_stale(held)
        failure = unavailable_stats(message)
        self.cache.store("stats", failure, now, STATS_ERROR_TTL)
        return failure

    def _load_token(self) -> dict:
        payload = self._get_json(DEX_API)
        pairs = payload.get("pairs") if isinstance(payload, dict) else None
        if not pairs:
            raise UpstreamError("DexScreener returned no pairs for this token.")
        return normalize_pair(pairs[0], self.clock.now())

    def _get_json(self, url: str, *, headers=None, params=None):
        response = self.client.get(url, headers=headers, params=params)
        response.raise_for_status()
        try:
            return response.json()
        except ValueError as exc:
            raise UpstreamError("The upstream response was not JSON.") from exc

    def _remember(self, key: str, body: dict, now: float, ttl: float) -> None:
        self.cache.store(key, body, now, ttl)
        self._write_disk(key, body)

    def _load_disk(self) -> None:
        if self.disk is None or not self.disk.is_file():
            return
        try:
            saved = orjson.loads(self.disk.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            logger.warning("could not read the local upstream cache")
            return
        if not isinstance(saved, dict):
            return
        for key in ("token", "stats"):
            value = saved.get(key)
            if isinstance(value, dict) and value.get("available"):
                self.cache.remember_expired(key, value)

    def _write_disk(self, key: str, body: dict) -> None:
        if self.disk is None or not body.get("available"):
            return
        try:
            with self._disk_lock:
                current: dict = {}
                if self.disk.is_file():
                    try:
                        loaded = orjson.loads(self.disk.read_text(encoding="utf-8"))
                        if isinstance(loaded, dict):
                            current = loaded
                    except (OSError, ValueError):
                        current = {}
                current[key] = body
                self.disk.parent.mkdir(parents=True, exist_ok=True)
                temporary = self.disk.with_suffix(".tmp")
                temporary.write_text(orjson.dumps(current).decode(), encoding="utf-8")
                temporary.replace(self.disk)
        except OSError:
            logger.warning("could not update the local upstream cache")

    @staticmethod
    def _log_failure(source: str, exc: Exception) -> None:
        status = getattr(getattr(exc, "response", None), "status_code", None)
        logger.warning("%s request failed (status=%s, error=%s)", source, status, type(exc).__name__)
