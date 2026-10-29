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


def build(routes, env=None, clock=None, disk=None):
    client = MapClient(routes)
    services = Services(
        client=client,
        cache=TtlCache(),
        clock=clock or ManualClock(),
        env={} if env is None else env,
        disk=disk,
    )
    return create_app(services), client


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
    assert "Stay risk averse." in text
    assert "Expect imposters. We’ll never DM you first." in text
    assert "Build things for humans." in text
    assert "Optimize for social impact and sustainability." in text
    assert text.index("Stay risk averse.") < text.index("Build things for humans.") < text.index("Expect imposters.") < text.index("Optimize for social")
    assert 'href="https://kennethreitz.org/essays/2013-01-be_cordial_or_be_on_your_way"' in text
    assert text.index('id="rules"') < text.index('id="intentions"')
    assert "February 14, 2027" in text
    assert "about 10.7%" in text
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


def test_trust_strip_lock_card_and_timeline_markup():
    import pathlib
    html = pathlib.Path("templates/index.html").read_text()
    assert "marked to confirm" not in html
    assert 'class="trust-strip"' in html and "@kennethreitz42" in html
    assert 'id="lock-progress"' in html and 'data-end="2027-02-14T05:00:00Z"' in html
    assert html.count('data-copy="') == 5
    assert "https://solscan.io/account/BaCrGRdrxHrovQjEK8D4PmjWPVCbeaZUPRKvPJ4WW5vo" in html
    assert 'class="timeline"' in html and "0.2.0" in html
    assert "Requests 2.0.0 is released on November 15." in html
    assert html.index("1.0.0") < html.index("2.0.0") < html.index('tl-year">PSF')
    assert "creator of the token" not in html.lower()


def test_section_anchors():
    import pathlib
    html = pathlib.Path("templates/index.html").read_text()
    for anchor in ("hero", "rules", "chat", "intentions", "lock", "library", "heritage", "market", "disclaimer", "links"):
        assert html.count(f'id="{anchor}"') == 1, anchor
    for anchor in ("rules", "intentions", "lock", "library", "heritage", "market", "disclaimer", "links"):
        assert f'<a class="anchor" href="#{anchor}"' in html, anchor
    for nav in ("#intentions", "#library", "#market", "#disclaimer"):
        assert f'href="{nav}"' in html
    css = pathlib.Path("static/css/site.css").read_text()
    assert "scroll-margin-top" in css


def test_fomo_link():
    from requeststoken.config import FOMO_PAGE, PRIMARY_LINKS
    assert FOMO_PAGE == "https://fomo.family/tokens/solana/6ceuPfcE67PLs3Z6CbJpFpqNyAevVocP2zsDkbM1pump"
    assert any(link["href"] == FOMO_PAGE for link in PRIMARY_LINKS)
    import pathlib
    html = pathlib.Path("templates/index.html").read_text()
    assert "Also trade and follow on" in html
    assert html.count("{{ fomo_page }}") == 2


def test_thesis_holders_and_listing_note():
    import pathlib
    html = pathlib.Path("templates/index.html").read_text()
    assert 'id="thesis"' in html and 'href="#rules">five rules' in html and 'href="#lock">' in html
    assert "didn’t launch it" in html and "largest holder" in html
    assert "his wife needs a new car" in html
    assert "DexScreener currently lists requeststoken.com" not in html
    assert "42.0% of supply" in html and "Solana mainnet RPC" in html


def test_group_chat_join_link():
    import pathlib
    html = pathlib.Path("templates/index.html").read_text()
    assert html.count('id="chat"') == 1
    assert 'href="https://x.com/i/chat/group_join/g2113555281518977367/54IJSMh16G"' in html
    assert "Join the group chat" in html
    assert "DM @kennethreitz42" not in html and "If the link doesn" not in html and 'class="chat-alt"' not in html
    assert "no Telegram and no Discord" not in html
    assert 'class="chat-join" id="chat-telegram" href="https://t.me/+dz8vzHYMS78xMzA1"' in html
    assert "Join the Telegram group" in html
    assert "t.me/+On-0RCC4YfE2Yjdl" not in html
    assert "X is home" not in html and "very experimental" not in html
    assert "the only real links" not in html
    assert "gathers in two places: a group chat on X and a Telegram group" in html
    assert "There is no Discord" in html and "the real links are the two on this page" in html
    assert html.count('class="chat-join"') == 2
    assert "never DM you first" in html


def test_observatory_link_is_a_button():
    import pathlib
    html = pathlib.Path("templates/index.html").read_text()
    assert '<a class="obs-cta" href="/observatory">Explore download history and project usage →</a>' in html
    assert ".obs-cta {" in pathlib.Path("static/css/site.css").read_text()


def test_about_order_and_community_links():
    import pathlib
    from requeststoken.config import PRIMARY_LINKS
    html = pathlib.Path("templates/index.html").read_text()
    order = ["<h3>Written about Kenneth</h3>", "<h3>Talks</h3>", "<h3>Other interesting reading</h3>", "<h3>Family history</h3>", "<h3>Open-source genome data</h3>", 'class="fine">Source:']
    idx = [html.index(h) for h in order]
    assert idx == sorted(idx)
    assert html.index("<h3>Talks</h3>") < html.index("skYBOXE02OQ") < html.index("bpZS9ehw98k") < html.index("<h3>Other interesting reading</h3>")
    assert "<h3>Lineage</h3>" not in html
    assert '<a href="#chat">Community</a>' in html
    hrefs = [l["href"] for l in PRIMARY_LINKS]
    assert "https://t.me/+dz8vzHYMS78xMzA1" in hrefs
    assert "https://x.com/i/chat/group_join/g2113555281518977367/54IJSMh16G" in hrefs


def test_other_projects_list():
    import pathlib
    html = pathlib.Path("templates/index.html").read_text()
    block = html[html.index("<h3>Other projects by Kenneth</h3>"):html.index("<h3>Written about Kenneth</h3>")]
    for href in ("https://pytheory.org", "https://interpretations.kennethreitz.org", "https://kjvstudy.org", "https://kennethreitz.org", "https://github.com/kennethreitz"):
        assert f'href="{href}"' in block
    assert html.index("05 — About") < html.index("<h3>Other projects by Kenneth</h3>")
    assert "short-version" not in html


def test_other_projects_source_links():
    import pathlib
    html = pathlib.Path("templates/index.html").read_text()
    block = html[html.index("<h3>Other projects by Kenneth</h3>"):html.index("<h3>Written about Kenneth</h3>")]
    assert "All open source: each project’s source is public on GitHub under an MIT or ISC license." in block
    assert 'href="https://github.com/kennethreitz" target="_blank"' in block
    assert "context.kennethreitz.org" not in block and "kennethreitz/context" not in block
    for r in ("pytheory", "interpretations", "kjvstudy.org", "kennethreitz.org"):
        assert f'href="https://github.com/kennethreitz/{r}" target="_blank" rel="noopener noreferrer">source</a>' in block


def test_faq_help_safety_updates_origin():
    import pathlib
    html = pathlib.Path("templates/index.html").read_text()
    assert html.index('id="intentions"') < html.index('id="faq"') < html.index('id="library"')
    assert "Is there an official $requests X account?" in html
    assert "@RequestsToken and requeststoken.com are not his" in html
    assert "listed on this page first" in html
    assert "What’s the utility?" in html and "no web3 features are planned" in html
    assert "requests.py lived inside that project until he split it into its own repository in 2011" in html
    assert html.index('tl-year">Origin') < html.index('tl-year">2011')
    assert "How you can help" in html and "no raids, and no paid promoters" in html
    assert "Real DexScreener profile updates are paid directly to DexScreener." in html
    assert "bot or admin access" in html
    dates = [d for d in ("2026-10-21", "2026-10-24", "2026-10-25", "2026-10-27", "2026-10-28", "2026-10-29")]
    idx = [html.index(f'<time datetime="{d}">') for d in dates]
    assert idx == sorted(idx)
    assert "his wife needs a new car" in html and "short-version" not in html
    assert '<a href="#faq">FAQ</a>' in html
