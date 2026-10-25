"""Real Redis coordination with scripted upstream responses (no external HTTP)."""

import copy
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from requeststoken.data import ManualClock, Services
from requeststoken.observatory import DownloadObservatory, adoption_manifest, queries_for, validate_snapshot
from requeststoken.web import create_app


def seed():
    return {
        "package": "requests", "firstDate": "2026-10-23", "latestDate": "2026-10-24",
        "rawTotal": 300, "rawLatest": 200, "fetchedAt": "2026-10-25T00:00:00Z",
        "daily": [{"date": "2026-10-23", "downloads": 100}, {"date": "2026-10-24", "downloads": 200}],
    }


class QueueExecutor:
    def __init__(self): self.jobs = []
    def submit(self, function, *args): self.jobs.append((function, args))
    def run(self):
        function, args = self.jobs.pop(0)
        function(*args)


class ScriptedClient:
    def __init__(self, bodies): self.bodies, self.calls = list(bodies), []
    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        result = self.bodies.pop(0)
        if isinstance(result, Exception): raise result
        class Response:
            def raise_for_status(self): pass
            def json(self): return result
        return Response()


def result(rows): return {"data": rows, "rows": len(rows)}


def make(redis_store, bodies=(), **kwargs):
    clock = kwargs.pop("clock", ManualClock(datetime(2026, 10, 25, 12, tzinfo=timezone.utc)))
    executor = kwargs.pop("executor", QueueExecutor())
    client = ScriptedClient(bodies)
    service = DownloadObservatory(store=redis_store, env={}, seed=seed(), client=client,
                                  clock=clock, executor=executor, **kwargs)
    return service, client, clock, executor


def test_first_request_is_immediate_and_one_refresh_is_shared_between_instances(redis_store):
    one, client, clock, jobs = make(redis_store)
    first = one.read()
    assert first["status"] == "fallback" and first["refreshing"]
    assert first["snapshot"]["rawTotal"] == 300
    assert not client.calls and len(jobs.jobs) == 1
    two, _, _, other_jobs = make(redis_store, clock=clock)
    assert two.read()["refreshing"]
    assert not other_jobs.jobs
    assert one.read()["snapshot"]["rawTotal"] == 300
    assert len(jobs.jobs) == 1


def test_success_replaces_counts_and_reuses_snapshot_until_hourly_refresh(redis_store):
    daily = seed()["daily"] + [{"date": "2026-10-25", "downloads": "40"}]
    service, client, clock, jobs = make(redis_store, [result(daily), result([{"downloads": "340"}]), result([{"downloads": 40}])])
    service.read(); jobs.run()
    body = service.read()
    assert body["status"] == "current" and not body["refreshing"]
    assert body["snapshot"]["rawTotal"] == 340
    assert body["snapshot"]["daily"][-1]["downloads"] == 40
    assert len(client.calls) == 3
    # Timers belong to Redis, not any worker's clock.
    clock.advance(3601); assert not service.read()["refreshing"]
    assert 3590 <= redis_store.client.ttl(redis_store.cooldown_key) <= 3600
    redis_store.client.pexpireat(redis_store.cooldown_key, 1)
    assert service.read()["refreshing"]
    check = json.loads(redis_store.client.hget(redis_store.state_key, "check:2026-10-25"))
    assert (check["rawTotal"], check["reportedDays"]) == (340, 3)
    assert redis_store.client.ttl(redis_store.state_key) == -1


def test_failures_preserve_snapshot_time_and_back_off_then_accept_corrections(redis_store):
    service, client, clock, jobs = make(redis_store, [RuntimeError("offline")])
    first = service.read(); jobs.run()
    failed = service.read()
    assert failed["status"] == "cached"
    assert failed["snapshot"] == first["snapshot"]
    assert failed["snapshot"]["fetchedAt"] == "2026-10-25T00:00:00Z"
    assert failed["checkedAt"] != failed["snapshot"]["fetchedAt"]
    clock.advance(599); service.read(); assert not jobs.jobs
    redis_store.client.pexpireat(redis_store.cooldown_key, 1)
    client.bodies.extend([result([{"date": "2026-10-23", "downloads": 90}, {"date": "2026-10-24", "downloads": 190}]), result([{"downloads": 280}]), result([{"downloads": 190}])])
    service.read(); jobs.run()
    assert service.read()["snapshot"]["rawTotal"] == 280


def test_expired_lease_can_be_reclaimed_without_old_worker_overwriting_new_data(redis_store):
    old, _, clock, jobs = make(redis_store, [RuntimeError("late failure")])
    old.read(); redis_store.client.pexpireat(redis_store.lease_key, 1)
    new, _, _, newer_jobs = make(redis_store, [result(seed()["daily"]), result([{"downloads": 310}]), result([{"downloads": 200}])], clock=clock)
    new.read(); newer_jobs.run(); jobs.run()
    assert new.read()["status"] == "current"
    assert new.read()["snapshot"]["rawTotal"] == 310


@pytest.mark.parametrize("rows", [
    [{"date": "2026-10-24", "downloads": 200}],
    [{"date": "2026-10-23", "downloads": 100}, {"date": "2026-10-26", "downloads": 200}],
    [{"date": "2026-10-24", "downloads": 200}, {"date": "2026-10-23", "downloads": 100}],
    [{"date": "2026-10-23", "downloads": 100}, {"date": "2026-10-23", "downloads": 100}],
])
def test_bad_history_cannot_replace_last_snapshot(redis_store, rows):
    service, _, _, jobs = make(redis_store, [result(rows), result([{"downloads": 300}]), result([{"downloads": 200}])])
    first = service.read(); jobs.run()
    assert service.read()["status"] == "cached"
    assert service.read()["snapshot"] == first["snapshot"]


def test_truncated_responses_are_errors_even_with_plausible_data(redis_store):
    body = result(seed()["daily"]); body["rows_before_limit_at_least"] = 3
    service, _, _, jobs = make(redis_store, [body])
    service.read(); jobs.run()
    assert service.read()["status"] == "cached"


@pytest.mark.parametrize("bad", [-1, 1.5, True, "NaN", 2**53])
def test_invalid_counts_rejected(bad):
    value = seed(); value["daily"][0]["downloads"] = bad
    with pytest.raises(ValueError): validate_snapshot(value)


def test_queries_include_current_day_and_fail_instead_of_truncating():
    queries = queries_for("2026-10-25", "2026-10-24")
    assert "date<='2026-10-25'" in queries["daily"]
    assert "date<'2026-10-25'" in queries["total"]
    assert all("read_overflow_mode='throw'" in sql for sql in queries.values())
    with pytest.raises(ValueError): queries_for("2026-10-25' OR 1=1", "2026-10-24")


def test_no_redis_and_disabled_refresh_still_serve_seed():
    service, client, _, jobs = make(None, refresh_enabled=False)
    assert not service.read()["refreshing"] and not jobs.jobs and not client.calls
    assert service.read()["snapshot"]["rawTotal"] == 300
    assert service.read()["status"] == "fallback"


def test_no_redis_does_not_start_uncoordinated_upstream_requests():
    service, client, _, jobs = make(None)
    assert not service.read()["refreshing"] and not jobs.jobs and not client.calls
    assert service.read()["status"] == "fallback"


def test_routes_render_directory_and_preserve_existing_stats_contract(redis_store):
    service, _, _, _ = make(redis_store, refresh_enabled=False)
    api = create_app(Services(client=ScriptedClient([]), disk=None), observatory=service)
    response = api.requests.get("/observatory")
    assert response.status_code == 200
    text = response.text
    assert "Historically used in the official Python SDK" in text
    assert "A realtime event feed is not connected" in text
    assert "120 reviewed projects" in text
    assert 'src="/static/js/observatory.mjs"' in text
    assert "script-src 'self'" in response.headers["content-security-policy"]
    stats = api.requests.get("/api/observatory")
    assert stats.status_code == 200 and stats.json()["snapshot"]["rawTotal"] == 300
    assert stats.headers["cache-control"] == "no-store"
    assert api.requests.get("/api/requests").json()["source"] == "unavailable"


def test_reviewed_manifest_keeps_scopes_versions_pinned_proof_and_assets():
    manifest = adoption_manifest()
    assert len(manifest["entries"]) == 120
    assert len({x["organization"] for x in manifest["entries"]}) == 116
    assert len({x["id"] for x in manifest["entries"]}) == 120
    for entry in manifest["entries"]:
        assert len(entry["commitSha"]) == 40
        assert "/blob/" + entry["commitSha"] + "/" in entry["evidenceUrl"]
        assert entry["usageDetails"]["scopeNote"]
        assert len(entry["usageDetails"]["flow"]) == 3
        assert Path(entry["avatarPath"].lstrip("/")).is_file()
        assert len(entry["sourceSha256"]) == 64
    historical = manifest["historicalSdkUsage"]
    assert {(x["organization"], x["version"]) for x in historical} == {("OpenAI", "0.28.1"), ("Anthropic", "0.2.9")}
    assert all("Historical evidence" in x["scopeNote"] for x in historical)


def test_json_script_embedding_cannot_terminate_script():
    from requeststoken.web import safe_json
    text = safe_json({"text": "</script><script>alert(1)</script>&"})
    assert "</script>" not in text and "&" not in text
    assert json.loads(text)["text"].startswith("</script>")
