"""Failure, concurrency and migration checks against an isolated Redis server."""

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import pytest
from redis.exceptions import ConnectionError

from requeststoken.data import ManualClock
from requeststoken.observatory import DownloadObservatory
from requeststoken.observatory_migrate import import_legacy, load_legacy
from requeststoken.observatory_store import RedisSnapshotStore
from test_observatory import make, result, seed


def good_responses(total=340):
    return [result(seed()["daily"]), result([{"downloads": total}]), result([{"downloads": 200}])]


def test_simultaneous_workers_share_one_refresh(redis_store):
    services = [make(RedisSnapshotStore(redis_store.client, redis_store.prefix))
                for _ in range(12)]
    with ThreadPoolExecutor(max_workers=12) as executor:
        bodies = list(executor.map(lambda s: s[0].read(), services))
    assert all(body["refreshing"] for body in bodies)
    assert sum(len(s[3].jobs) for s in services) == 1
    assert 110 <= redis_store.client.ttl(redis_store.lease_key) <= 120


@pytest.mark.parametrize("late_result", ["success", "failure"])
def test_expired_worker_cannot_publish_or_release_a_new_lease(redis_store, late_result):
    old, _, _, old_jobs = make(redis_store, good_responses(999) if late_result == "success" else [RuntimeError()])
    old.read()
    redis_store.client.pexpireat(redis_store.lease_key, 1)
    new, _, _, new_jobs = make(redis_store, good_responses(350))
    new.read()
    token = redis_store.client.get(redis_store.lease_key)
    old_jobs.run()
    assert redis_store.client.get(redis_store.lease_key) == token
    assert not redis_store.client.exists(redis_store.cooldown_key)
    new_jobs.run()
    assert new.read()["snapshot"]["rawTotal"] == 350


def test_lease_expiry_rejects_a_late_success_even_without_a_successor(redis_store):
    service, _, _, jobs = make(redis_store, good_responses())
    service.read()
    redis_store.client.pexpireat(redis_store.lease_key, 1)
    jobs.run()
    assert not redis_store.client.exists(redis_store.state_key, redis_store.cooldown_key)


def test_shared_backoff_does_not_depend_on_worker_clock(redis_store):
    failed, _, _, jobs = make(redis_store, [RuntimeError()])
    failed.read(); jobs.run()
    ahead = ManualClock(datetime(2026, 10, 28, tzinfo=timezone.utc))
    another, _, _, more_jobs = make(redis_store, clock=ahead)
    assert another.read()["status"] == "cached"
    assert not more_jobs.jobs
    assert 590 <= redis_store.client.ttl(redis_store.cooldown_key) <= 600


class UnavailableStore:
    def read_and_claim(self, *args):
        raise ConnectionError("sensitive connection detail must not appear in logs")
    def publish(self, *args, **kwargs):
        raise ConnectionError("sensitive connection detail must not appear in logs")


def test_redis_outage_keeps_last_observed_snapshot_and_recovers(redis_store, caplog):
    service, client, _, jobs = make(redis_store, good_responses())
    service.read(); jobs.run()
    saved = service.read()
    service.store = UnavailableStore()
    offline = service.read()
    assert offline["status"] == "fallback" and not offline["refreshing"]
    assert offline["snapshot"] == saved["snapshot"]
    assert offline["checkedAt"] == saved["checkedAt"]
    assert not jobs.jobs and len(client.calls) == 3
    assert "sensitive connection detail" not in caplog.text
    service.store = redis_store
    assert service.read()["status"] == "current"


def test_new_process_and_disabled_refresh_use_shared_history(redis_store):
    first, _, _, jobs = make(redis_store, good_responses())
    first.read(); jobs.run()
    fresh, client, _, more_jobs = make(redis_store, refresh_enabled=False)
    response = fresh.read()
    assert response["status"] == "current" and response["snapshot"]["rawTotal"] == 340
    assert not more_jobs.jobs and not client.calls


def test_lost_redis_during_publish_does_not_claim_a_success(redis_store, caplog):
    service, _, _, jobs = make(redis_store, good_responses())
    service.read(); service.store = UnavailableStore(); jobs.run()
    assert service.read()["status"] == "fallback"
    assert service.read()["snapshot"]["rawTotal"] == 300
    assert "sensitive connection detail" not in caplog.text


@pytest.mark.parametrize("payload", ["not json", '{"snapshot":null}', json.dumps({
    "snapshot": seed(), "checkedAt": "2026-10-25", "status": "current",
})])
def test_corrupt_payload_falls_back_and_can_be_repaired(redis_store, payload):
    redis_store.client.hset(redis_store.state_key, "snapshot", payload)
    service, _, _, jobs = make(redis_store, good_responses())
    response = service.read()
    assert response["status"] == "fallback" and response["snapshot"]["rawTotal"] == 300
    jobs.run()
    assert service.read()["status"] == "current"


def test_failed_job_submission_releases_lease_and_backs_off(redis_store):
    class ClosedExecutor:
        def submit(self, *args):
            raise RuntimeError("executor closed")
    service, client, _, _ = make(redis_store, executor=ClosedExecutor())
    assert service.read()["status"] == "cached"
    assert not redis_store.client.exists(redis_store.lease_key)
    assert redis_store.client.ttl(redis_store.cooldown_key) > 0
    assert not client.calls


def test_checks_survive_calendar_rollover_and_latest_snapshot_corrections(redis_store):
    service, client, clock, jobs = make(redis_store, good_responses())
    service.read(); jobs.run()
    clock.advance(24 * 3600)
    redis_store.client.pexpireat(redis_store.cooldown_key, 1)
    client.bodies.extend(good_responses(330))
    service.read(); jobs.run()
    records = redis_store.client.hgetall(redis_store.state_key)
    assert json.loads(records["check:2026-10-25"])["rawTotal"] == 340
    assert json.loads(records["check:2026-10-26"])["rawTotal"] == 330
    assert service.read()["snapshot"]["rawTotal"] == 330
    assert redis_store.client.ttl(redis_store.state_key) == -1


def test_aof_retains_snapshot_and_history_after_redis_process_crash(redis_store, redis_instance):
    service, _, _, jobs = make(redis_store, good_responses())
    service.read(); jobs.run()
    expected = redis_store.client.hgetall(redis_store.state_key)
    redis_instance.restart(crash=True)
    assert redis_store.client.hgetall(redis_store.state_key) == expected
    fresh, _, _, more_jobs = make(redis_store)
    assert fresh.read()["snapshot"]["rawTotal"] == 340
    assert not more_jobs.jobs


def test_redis_urls_are_optional_and_invalid_configuration_is_not_exposed(caplog):
    service = DownloadObservatory(seed=seed(), env={"REDIS_URL": "bad://secret:password@host"})
    assert service.read()["status"] == "fallback"
    assert "secret" not in caplog.text and "password" not in caplog.text


def test_configuration_creates_a_bounded_client_without_connecting():
    service = DownloadObservatory(seed=seed(), env={
        "REDIS_URL": "rediss://example.test:6380/0", "REQUESTS_OBSERVATORY_REDIS_PREFIX": "preview",
    })
    options = service.store.client.connection_pool.connection_kwargs
    assert options["socket_timeout"] == options["socket_connect_timeout"] == 1
    assert options["decode_responses"] is True
    assert service.store.state_key == "{preview}:state"
    assert service.store.client.connection_pool.max_connections == 16


def legacy_file(tmp_path):
    path = tmp_path / "legacy.sqlite"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE snapshot (id TEXT, payload TEXT, checked_at TEXT, status TEXT)")
        db.execute("CREATE TABLE source_checks (day TEXT, fetched_at TEXT, latest_date TEXT, raw_total INTEGER, raw_latest INTEGER, reported_days INTEGER)")
        db.execute("INSERT INTO snapshot VALUES (?,?,?,?)", ("requests", json.dumps(seed()), seed()["fetchedAt"], "current"))
        db.execute("INSERT INTO source_checks VALUES (?,?,?,?,?,?)", ("2026-10-25", seed()["fetchedAt"], "2026-10-24", 300, 200, 2))
    return path


def test_sqlite_import_preserves_source_and_will_not_overwrite_redis(tmp_path, redis_store):
    path = legacy_file(tmp_path)
    original = path.read_bytes()
    assert len(load_legacy(path)) == 2
    assert not redis_store.client.exists(redis_store.state_key)
    assert import_legacy(path, redis_store) == 1
    assert path.read_bytes() == original
    service, _, _, _ = make(redis_store, refresh_enabled=False)
    assert service.read()["status"] == "current"
    assert service.read()["snapshot"]["rawTotal"] == 300
    assert redis_store.client.hget(redis_store.state_key, "check:2026-10-25")
    assert redis_store.client.ttl(redis_store.state_key) == -1
    with pytest.raises(ValueError, match="not empty"):
        import_legacy(path, redis_store)


def test_import_refuses_an_active_refresh_and_missing_source(tmp_path, redis_store):
    service, _, _, _ = make(redis_store)
    service.read()
    with pytest.raises(ValueError, match="not empty"):
        import_legacy(legacy_file(tmp_path), redis_store)
    with pytest.raises(sqlite3.OperationalError):
        load_legacy(tmp_path / "missing.sqlite")
    assert not (tmp_path / "missing.sqlite").exists()
