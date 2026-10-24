"""JSON endpoint tests. Upstream HTTP is scripted; nothing here calls the network."""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone

import httpx

from requeststoken.config import CONTRACT, DEX_API, PYPI_OVERALL, PYPI_RECENT
from requeststoken.data import ManualClock, Services, TtlCache
from requeststoken.web import create_app

PAIR = {
    "chainId": "solana",
    "dexId": "pumpswap",
    "url": "https://dexscreener.com/solana/pair111",
    "pairAddress": "PAIR111",
    "baseToken": {
        "address": CONTRACT,
        "name": "requests",
        "symbol": "requests",
    },
    "quoteToken": {"address": "So111", "name": "Wrapped SOL", "symbol": "SOL"},
    "priceNative": "0.000001018",
    "priceUsd": "0.0001059",
    "txns": {"h24": {"buys": 1235, "sells": 901}},
    "volume": {"h24": 146317.04},
    "priceChange": {"h24": -14.46},
    "liquidity": {"usd": 28392.74},
    "marketCap": 101143,
    "pairCreatedAt": 1792514856000,
}

DEX = {"pairs": [PAIR, {"marketCap": 999999, "baseToken": {"address": "not-this-token"}}]}

RECENT = {
    "data": {"last_day": 250, "last_week": 1000, "last_month": 5000},
    "package": "requests",
    "type": "recent_downloads",
}

OVERALL = {
    "data": [
        {"category": "without_mirrors", "date": "2026-10-21", "downloads": 100},
        {"category": "without_mirrors", "date": "2026-10-22", "downloads": 250},
        {"category": "with_mirrors", "date": "2026-10-22", "downloads": 99999},
    ],
    "package": "requests",
    "type": "overall_downloads",
}

class FakeResponse:
    def __init__(self, payload, status_code=200):
        self.status_code = status_code
        self._payload = payload
        self.request = httpx.Request("GET", "https://example.test/upstream")

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            response = httpx.Response(self.status_code, request=self.request)
            raise httpx.HTTPStatusError(
                str(self.status_code), request=self.request, response=response
            )


class MapClient:
    """Returns queued responses by URL. Unexpected URLs fail closed."""

    def __init__(self, routes):
        self.routes = {url: list(items) for url, items in routes.items()}
        self.calls = []
        self._lock = threading.Lock()

    def get(self, url, headers=None, params=None):
        with self._lock:
            self.calls.append({"url": url, "headers": dict(headers or {}), "params": params})
            queue = self.routes.get(url)
            if not queue:
                raise RuntimeError(f"no scripted response for {url}")
            item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def build(routes, env=None, clock=None, disk=None, environ=None):
    client = MapClient(routes)
    services = Services(
        client=client,
        cache=TtlCache(),
        clock=clock or ManualClock(),
        env={} if env is None else env,
        disk=disk,
    )
    return create_app(services, environ=environ), client


def test_token_endpoint_uses_first_pair_only():
    api, client = build({DEX_API: [FakeResponse(DEX)]})
    response = api.requests.get("/api/token")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    body = response.json()
    assert body["available"] is True
    assert body["stale"] is False
    assert body["contract"] == CONTRACT
    assert body["market_cap_usd"] == 101143
    assert body["market_cap_display"] == "$101,143"
    assert body["price_display"] == "$0.0001059"
    assert body["price_native_display"] == "0.000001018 SOL"
    assert body["change_display"] == "−14.46%"
    assert body["change_direction"] == "down"
    assert body["liquidity_display"] == "$28,392.74"
    assert body["volume_display"] == "$146,317.04"
    assert body["buys_h24"] == 1235
    assert body["sells_h24"] == 901
    assert body["activity_display"] == "1,235 / 901"
    assert body["dex_display"] == "PumpSwap"
    assert body["pair_url"] == "https://dexscreener.com/solana/pair111"
    assert "2026" in body["listed_on"]
    assert client.calls[0]["url"] == DEX_API


def test_token_endpoint_caches_then_serves_stale_then_retries():
    clock = ManualClock()
    api, client = build(
        {DEX_API: [FakeResponse(DEX), RuntimeError("down"), FakeResponse(DEX)]},
        clock=clock,
    )
    first = api.requests.get("/api/token").json()
    second = api.requests.get("/api/token").json()
    assert second["market_cap_usd"] == first["market_cap_usd"]
    assert len(client.calls) == 1

    clock.advance(21)
    stale = api.requests.get("/api/token").json()
    assert stale["available"] is True
    assert stale["stale"] is True
    assert stale["buys_h24"] == 1235
    assert len(client.calls) == 2

    clock.advance(10)
    api.requests.get("/api/token")
    assert len(client.calls) == 2

    clock.advance(16)
    fresh = api.requests.get("/api/token").json()
    assert fresh["stale"] is False
    assert len(client.calls) == 3


def test_token_endpoint_falls_back_when_upstream_fails():
    api, _client = build({DEX_API: [RuntimeError("down")]})
    response = api.requests.get("/api/token")
    assert response.status_code == 200
    body = response.json()
    assert body["available"] is False
    assert body["contract"] == CONTRACT
    assert "temporarily unavailable" in body["detail"]


def test_token_endpoint_reports_empty_pairs():
    api, _client = build({DEX_API: [FakeResponse({"pairs": []})]})
    body = api.requests.get("/api/token").json()
    assert body["available"] is False
    assert "no pairs" in body["detail"]


def test_token_endpoint_rejects_a_mismatched_first_pair():
    wrong = json.loads(json.dumps(PAIR))
    wrong["baseToken"]["address"] = "not-the-contract"
    api, _client = build({DEX_API: [FakeResponse({"pairs": [wrong]})]})
    body = api.requests.get("/api/token").json()
    assert body["available"] is False
    assert "does not match" in body["detail"]


def test_requests_endpoint_without_key_uses_pypistats_and_drops_mirrors():
    api, client = build({
        PYPI_RECENT: [FakeResponse(RECENT)],
        PYPI_OVERALL: [FakeResponse(OVERALL)],
    })
    response = api.requests.get("/api/requests")
    assert response.status_code == 200
    body = response.json()
    assert body["available"] is True
    assert body["source"] == "pypistats"
    assert body["total_downloads"] is None
    figures = {item["label"]: item["downloads"] for item in body["figures"]}
    assert figures == {"Last week": 1000, "Last month": 5000}
    assert [point["downloads"] for point in body["series"]] == [100, 250]
    assert "pypistats.org" in body["detail"]


def test_requests_endpoint_when_both_sources_fail():
    api, _client = build({
        PYPI_RECENT: [RuntimeError("pypi down")],
        PYPI_OVERALL: [RuntimeError("pypi down")],
    })
    response = api.requests.get("/api/requests")
    assert response.status_code == 200
    body = response.json()
    assert body["available"] is False
    assert body["series"] == []
    assert "temporarily unavailable" in body["detail"]


def test_disk_cache_is_used_when_a_later_process_cannot_reach_upstream(tmp_path):
    disk = tmp_path / "cache.json"
    clock = ManualClock()
    api, _client = build({DEX_API: [FakeResponse(DEX)]}, clock=clock, disk=disk)
    assert api.requests.get("/api/token").json()["available"] is True

    later, _client = build({DEX_API: [RuntimeError("down")]}, clock=clock, disk=disk)
    body = later.requests.get("/api/token").json()
    assert body["available"] is True
    assert body["stale"] is True
    assert body["market_cap_usd"] == 101143


def test_homepage_renders_facts_figures_and_the_disclaimer():
    api, _client = build({
        DEX_API: [FakeResponse(DEX)],
        PYPI_RECENT: [FakeResponse(RECENT)],
        PYPI_OVERALL: [FakeResponse(OVERALL)],
    })
    response = api.requests.get("/")
    assert response.status_code == 200
    text = response.text
    assert CONTRACT in text
    assert "$101,143" in text
    assert "1,000" in text
    assert "Be cordial, or be on your way." in text
    assert "stay risk averse." in text
    assert "February 14, 2027" in text
    assert "about 11%" in text
    assert "not financial advice" in text
    assert "afford to lose" in text
    assert "impersonators" in text
    assert "paid-trending" in text
    assert "official links" in text
    assert "TODO" not in text
    lowered = text.lower()
    assert "connect wallet" not in lowered
    assert "walletconnect" not in lowered
    assert "seed phrase" not in lowered
    assert "to the moon" not in lowered
    assert "guaranteed" not in lowered
    assert "https://pump.fun/coin/" + CONTRACT in text
    assert "https://dexscreener.com/solana/" + CONTRACT in text


def test_homepage_still_renders_when_upstreams_fail():
    api, _client = build({})
    response = api.requests.get("/")
    assert response.status_code == 200
    assert CONTRACT in response.text
    assert "temporarily unavailable" in response.text
    assert "not financial advice" in response.text


_UMAMI_ID = "11111111-1111-4111-8111-111111111111"
_UMAMI_SCRIPT = "https://analytics.kennethreitz.org/script.js"
_CSP_CLOSED = (
    "default-src 'self'; "
    "img-src 'self' data:; "
    "style-src 'self'; "
    "font-src 'self'; "
    "script-src 'self'; "
    "connect-src 'self'; "
    "base-uri 'self'; "
    "form-action 'none'; "
    "frame-ancestors 'none'"
)


def _homepage(environ):
    api, _client = build({}, environ=environ)
    response = api.requests.get("/")
    assert response.status_code == 200
    return response


def test_homepage_omits_umami_until_a_website_id_is_set():
    response = _homepage({})
    assert "data-website-id" not in response.text
    assert "analytics.kennethreitz.org" not in response.text
    assert response.headers["content-security-policy"] == _CSP_CLOSED


def test_homepage_loads_kenneths_umami_when_the_website_id_is_set():
    response = _homepage({"UMAMI_WEBSITE_ID": _UMAMI_ID})
    tag = (
        f'<script defer src="{_UMAMI_SCRIPT}" '
        f'data-website-id="{_UMAMI_ID}"></script>'
    )
    head, _, body = response.text.partition("</head>")
    assert tag in head
    assert "data-website-id" not in body
    assert '<h1><span class="dollar">$</span>requests</h1>' in body
    policy = response.headers["content-security-policy"]
    assert "script-src 'self' https://analytics.kennethreitz.org;" in policy
    assert "connect-src 'self' https://analytics.kennethreitz.org;" in policy


def test_umami_script_url_can_be_overridden():
    script = "https://stats.example.test:8443/umami/script.js"
    response = _homepage({
        "UMAMI_WEBSITE_ID": _UMAMI_ID,
        "UMAMI_SCRIPT_URL": script,
    })
    tag = f'<script defer src="{script}" data-website-id="{_UMAMI_ID}"></script>'
    assert tag in response.text.partition("</head>")[0]
    policy = response.headers["content-security-policy"]
    assert "https://stats.example.test:8443" in policy
    assert "analytics.kennethreitz.org" not in policy


def test_umami_tag_is_omitted_for_unusable_settings():
    cases = (
        {"UMAMI_WEBSITE_ID": "not-a-uuid"},
        {"UMAMI_WEBSITE_ID": "  "},
        {"UMAMI_WEBSITE_ID": _UMAMI_ID, "UMAMI_SCRIPT_URL": "javascript:alert(1)"},
        {"UMAMI_WEBSITE_ID": _UMAMI_ID, "UMAMI_SCRIPT_URL": "https://evil.test/not-a-script"},
        {"UMAMI_WEBSITE_ID": '<script>alert(1)</script>'},
    )
    for environ in cases:
        response = _homepage(environ)
        assert "data-website-id" not in response.text
        assert "<script defer" not in response.text
        assert response.headers["content-security-policy"] == _CSP_CLOSED
