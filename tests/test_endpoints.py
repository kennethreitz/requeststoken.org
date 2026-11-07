"""JSON endpoint tests. Upstream HTTP is scripted; nothing here calls the network."""

from __future__ import annotations
from site_pages import site_source, site_html, TEMPLATES

import json
import threading
from datetime import datetime, timezone

import httpx

from requeststoken.config import CONTRACT, DEX_API, PYPI_OVERALL, PYPI_RECENT
from requeststoken.data import ManualClock, Services, TtlCache, ohlcv_url
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


POOL = "6Bs7VnSBJPjKjYG7FLfRd1KXEKRB92s7MaxNngC9M9Bh"
SOL = "So11111111111111111111111111111111111111112"
HOUR = ohlcv_url(POOL, "hour")
DAY = ohlcv_url(POOL, "day")


def _near(actual, expected):
    assert abs(actual - expected) < 1e-12


def _pair_book(address):
    copied = json.loads(json.dumps(PAIR))
    copied["pairAddress"] = address
    return {"pairs": [copied, {"pairAddress": "9" * 40, "baseToken": {"address": "not-this-token"}}]}


def _gecko(rows, base=CONTRACT, quote=SOL):
    return {
        "data": {"attributes": {"ohlcv_list": rows}},
        "meta": {
            "base": {"address": base, "symbol": "requests"},
            "quote": {"address": quote, "symbol": "SOL"},
        },
    }


CANDLES = [
    [1794067200, 0.000046, 0.000047, 0.000045, 0.0000465, 12.5],
    [1794063600, 0.000044, 0.000045, 0.000043, 0.000044, 8],
    ["nope", 1, 1, 1, 1, 1],
    [1794060000, 0, 1, 1, 1, 1],
]


def test_candles_use_the_first_pair_and_sort_ohlc():
    api, client = build({
        DEX_API: [FakeResponse(_pair_book(POOL))],
        HOUR: [FakeResponse(_gecko(CANDLES))],
    })
    response = api.requests.get("/api/candles")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    assert response.headers["cache-control"] == "public, max-age=60"
    body = response.json()
    assert body["available"] is True
    assert body["stale"] is False
    assert body["source"] == "geckoterminal"
    assert body["contract"] == CONTRACT
    assert body["pair_address"] == POOL
    assert body["timeframe"] == "1h"
    assert body["timeframe_label"] == "1H"
    assert body["currency"] == "usd"
    assert body["chart_url"] == "https://www.geckoterminal.com/solana/pools/" + POOL
    assert [row["time"] for row in body["candles"]] == [1794063600, 1794067200]
    _near(body["candles"][0]["open"], 0.000044)
    _near(body["candles"][1]["close"], 0.0000465)
    assert body["candles"][1]["volume"] == 12.5
    assert client.calls[0]["url"] == DEX_API
    gecko = client.calls[1]
    assert gecko["url"] == HOUR
    assert gecko["params"]["aggregate"] == 1
    assert gecko["params"]["limit"] == 168
    assert gecko["params"]["currency"] == "usd"
    assert gecko["params"]["token"] == "base"
    assert gecko["headers"]["Accept"] == "application/json;version=20230203"
    assert "9" * 40 not in gecko["url"]


def test_four_hour_and_daily_candles_pick_their_aggregate():
    api, client = build({
        DEX_API: [FakeResponse(_pair_book(POOL)), FakeResponse(_pair_book(POOL))],
        HOUR: [FakeResponse(_gecko(CANDLES))],
        DAY: [FakeResponse(_gecko(CANDLES))],
    })
    four = api.requests.get("/api/candles?timeframe=4H").json()
    assert four["timeframe"] == "4h"
    assert four["timeframe_label"] == "4H"
    assert client.calls[1]["params"]["aggregate"] == 4
    assert client.calls[1]["params"]["limit"] == 180
    daily = api.requests.get("/api/candles?timeframe=1d").json()
    assert daily["timeframe"] == "1d"
    assert client.calls[-1]["url"] == DAY
    assert client.calls[-1]["params"]["aggregate"] == 1


def test_candles_cache_then_serve_stale_then_retry():
    clock = ManualClock()
    api, client = build({
        DEX_API: [
            FakeResponse(_pair_book(POOL)),
            RuntimeError("dex"),
            FakeResponse(_pair_book(POOL)),
        ],
        HOUR: [
            FakeResponse(_gecko(CANDLES)),
            RuntimeError("gecko"),
            FakeResponse(_gecko(CANDLES)),
        ],
    }, clock=clock)
    first = api.requests.get("/api/candles?timeframe=1h").json()
    second = api.requests.get("/api/candles?timeframe=1h").json()
    assert second["candles"] == first["candles"]
    assert len(client.calls) == 2

    clock.advance(91)
    stale = api.requests.get("/api/candles?timeframe=1h").json()
    assert stale["available"] is True
    assert stale["stale"] is True
    assert stale["candles"][0]["time"] == first["candles"][0]["time"]
    assert "last saved candles" in stale["detail"]
    assert len(client.calls) == 4

    clock.advance(10)
    api.requests.get("/api/candles?timeframe=1h")
    assert len(client.calls) == 4

    clock.advance(46)
    fresh = api.requests.get("/api/candles?timeframe=1h").json()
    assert fresh["stale"] is False
    assert len(client.calls) == 6


def test_candles_fail_closed_without_calling_gecko_for_a_bad_pair_or_timeframe():
    api, client = build({DEX_API: [FakeResponse(DEX)]})
    response = api.requests.get("/api/candles?timeframe=1h")
    missing = response.json()
    assert missing["available"] is False
    assert missing["candles"] == []
    assert response.headers["cache-control"] == "public, max-age=15"
    assert "temporarily unavailable" in missing["detail"]
    assert all("geckoterminal" not in call["url"] for call in client.calls)

    unknown, quiet = build({})
    body = unknown.requests.get("/api/candles?timeframe=15m").json()
    assert body["available"] is False
    assert body["timeframe"] is None
    assert body["candles"] == []
    assert quiet.calls == []


def test_candles_price_the_quote_side_when_that_side_is_the_token():
    inverted = _gecko(CANDLES, base=SOL, quote=CONTRACT)
    api, client = build({
        DEX_API: [FakeResponse(_pair_book(POOL))],
        HOUR: [FakeResponse(inverted), FakeResponse(inverted)],
    })
    body = api.requests.get("/api/candles?timeframe=1h").json()
    assert body["available"] is True
    assert body["pair_address"] == POOL
    gecko = [call for call in client.calls if call["url"] == HOUR]
    assert [call["params"]["token"] for call in gecko] == ["base", "quote"]


def test_candles_reject_a_pool_that_does_not_contain_the_token():
    foreign = _gecko(CANDLES, base=SOL, quote="not-this-token")
    api, client = build({
        DEX_API: [FakeResponse(_pair_book(POOL))],
        HOUR: [FakeResponse(foreign), FakeResponse(foreign)],
    })
    body = api.requests.get("/api/candles").json()
    assert body["available"] is False
    assert body["candles"] == []
    gecko = [call for call in client.calls if call["url"] == HOUR]
    assert len(gecko) == 1


def test_candles_empty_or_rate_limited_reads_do_not_invent_rows():
    api, client = build({
        DEX_API: [FakeResponse(_pair_book(POOL))],
        HOUR: [FakeResponse(_gecko([]))],
    })
    empty = api.requests.get("/api/candles").json()
    assert empty["available"] is False
    assert empty["candles"] == []

    limited, _calls = build({
        DEX_API: [FakeResponse(_pair_book(POOL))],
        HOUR: [FakeResponse({}, status_code=429)],
    })
    blocked = limited.requests.get("/api/candles").json()
    assert blocked["available"] is False
    assert "429" not in (blocked["detail"] or "")
    assert "temporarily unavailable" in blocked["detail"]
    assert client.calls[0]["url"] == DEX_API


def test_candles_disk_cache_survives_a_later_outage(tmp_path):
    disk = tmp_path / "cache.json"
    clock = ManualClock()
    api, _client = build({
        DEX_API: [FakeResponse(_pair_book(POOL))],
        HOUR: [FakeResponse(_gecko(CANDLES))],
    }, clock=clock, disk=disk)
    assert api.requests.get("/api/candles").json()["available"] is True

    later, _client = build({
        DEX_API: [RuntimeError("down")],
        HOUR: [RuntimeError("down")],
    }, clock=clock, disk=disk)
    body = later.requests.get("/api/candles").json()
    assert body["available"] is True
    assert body["stale"] is True
    assert body["pair_address"] == POOL
    assert body["candles"][1]["volume"] == 12.5


def test_market_chart_keeps_the_stats():
    api, _client = build({DEX_API: [FakeResponse(DEX)]})
    response = api.requests.get("/")
    html = response.text
    for stat in ("stat-mcap", "stat-price", "stat-change", "stat-liquidity", "stat-volume", "stat-activity"):
        assert f'id="{stat}"' in html
    assert 'id="market-chart"' in html
    assert 'data-candle="1h"' in html and 'data-candle="4h"' in html and 'data-candle="1d"' in html
    assert "lightweight-charts.standalone.production.js" in html
    assert "/static/js/market-chart.js" in html
    assert 'id="chart"' not in html
    market = html.split('id="market"', 1)[1].split('id="disclaimer"', 1)[0]
    assert "financial advice" not in market.lower()
    assert "creator of the token" not in market.lower()
    policy = response.headers["content-security-policy"]
    assert "style-src 'self' 'unsafe-inline'" in policy
    assert "script-src 'self'" in policy
    assert "script-src 'unsafe-inline'" not in policy


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


def test_support_pitch_is_honest_and_first():
    import pathlib
    home = pathlib.Path("templates/home.html").read_text()
    assert home.index('id="support"') < home.index('id="hero"')
    assert "The work stays free." in home
    assert "February 14, 2011" in home
    assert "He didn’t launch this token." in home
    assert "Creator fees help support Kenneth’s ongoing work." in home
    assert "102.5M" in home and "Feb 14, 2027" in home
    assert "Release 9.2.0 is current" in home
    assert "Version 0.57.13" in home
    assert "24-track album" in home
    assert "April 2022" in home
    assert "This is not an investment, and it is not financial advice." in home
    assert "Nothing here is a promise of price, returns, or gains." in home
    for href in (
        "https://github.com/psf/requests",
        "https://github.com/kennethreitz/responder",
        "https://pytheory.org",
        "https://kjvstudy.org",
        "https://interpretations.kennethreitz.org",
        "https://github.com/kennethreitz/infinite-state-sample-pack",
    ):
        assert f'href="{href}"' in home
    assert "mcp-applemusic" not in home and "MCP servers" not in home
    assert "infinitestate.com" not in home
    lowered = home.lower()
    assert "charity" not in lowered and "charitable" not in lowered
    assert "The project’s official X account is {{ project_x() }}." in home
    assert "Kenneth’s personal account is {{ x() }}." in home
    assert "@RequestsToken (no underscore)" in home
    assert "We’re planning to launch an official X account soon." not in home
    assert 'rel="icon"' in pathlib.Path("templates/_macros.html").read_text()


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
    assert "1,000" in api.requests.get("/library").text
    assert "$101,143" in text
    community = api.requests.get("/community").text
    assert 'id="rules"' not in text and 'href="/community"' in text
    text, home = community, text
    assert "Be cordial, or be on your way." in text
    assert "Stay risk averse." in text
    assert "Expect imposters. We’ll never DM you first." in text
    assert "Build things for humans." in text
    assert "Optimize for social impact and sustainability." in text
    assert text.index("Stay risk averse.") < text.index("Build things for humans.") < text.index("Expect imposters.") < text.index("Optimize for social")
    assert 'href="https://kennethreitz.org/essays/2013-01-be_cordial_or_be_on_your_way"' in text
    assert text.index('id="rules"') < text.index('id="chat"')
    text = home
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
    html = site_source()
    assert "marked to confirm" not in html
    assert 'class="trust-strip"' in html and "@kennethreitz42" in html and "@Requests_Token" in html
    assert 'id="lock-progress"' in html and 'data-end="2027-02-14T05:00:00Z"' in html
    assert html.count('data-copy="') == 5
    assert "https://solscan.io/account/BaCrGRdrxHrovQjEK8D4PmjWPVCbeaZUPRKvPJ4WW5vo" in html
    assert 'class="timeline"' in html and "0.2.0" in html
    assert "Requests 2.0.0 is released on November 15." in html
    assert html.index("1.0.0") < html.index("2.0.0") < html.index('tl-year">PSF')
    assert "creator of the token" not in html.lower()


def test_section_anchors():
    import pathlib
    html = site_source()
    for anchor in ("hero", "rules", "chat", "intentions", "lock", "library", "heritage", "market", "disclaimer", "links"):
        assert html.count(f'id="{anchor}"') >= 1, anchor
        for name in TEMPLATES:
            assert pathlib.Path(f"templates/{name}.html").read_text().count(f'id="{anchor}"') <= 1, (name, anchor)
    for anchor in ("rules", "intentions", "lock", "library", "heritage", "market", "disclaimer", "links"):
        assert f'<a class="anchor" href="#{anchor}"' in html, anchor
    for nav in ("/community", "/faq", "/safety", "/updates", "/library", "/projects", "/about", "/observatory"):
        assert f'href="{nav}"' in html
    css = pathlib.Path("static/css/rt.css").read_text()
    assert "scroll-padding-top" in css


def test_fomo_link():
    from requeststoken.config import FOMO_PAGE, PRIMARY_LINKS
    assert FOMO_PAGE == "https://fomo.family/tokens/solana/6ceuPfcE67PLs3Z6CbJpFpqNyAevVocP2zsDkbM1pump"
    assert any(link["href"] == FOMO_PAGE for link in PRIMARY_LINKS)
    import pathlib
    html = site_source()
    assert "Also trade and follow on" in html
    assert html.count("{{ fomo_page }}") == 2


def test_thesis_holders_and_listing_note():
    import pathlib
    html = site_source()
    assert 'id="thesis"' in html and 'href="/community#rules">five rules' in html and 'href="#lock">' in html
    assert "didn’t launch it" in html and "largest holder" in html
    assert "his wife needs a new car" in html
    assert "DexScreener currently lists requeststoken.com" not in html
    assert "42.0% of supply" in html and "Solana mainnet RPC" in html


def test_group_chat_join_link():
    import pathlib
    html = site_source()
    assert html.count('id="chat"') == 1
    assert 'href="https://x.com/i/chat/group_join/g2113555281518977367/54IJSMh16G"' in html
    assert "Join the group chat" in html
    assert "DM @kennethreitz42" not in html and "If the link doesn" not in html and 'class="chat-alt"' not in html
    assert "no Telegram and no Discord" not in html
    assert 'class="chat-join btn" id="chat-telegram" href="https://t.me/+dz8vzHYMS78xMzA1"' in html
    assert "Telegram group (read-only)" in html
    assert "t.me/+On-0RCC4YfE2Yjdl" not in html
    assert "X is home" not in html and "very experimental" not in html
    assert "the only real links" not in html
    assert "gathers in the group chat on X. The Telegram group is locked and read-only" in html
    assert "There is no Discord" in html and "the real links are the ones on this page" in html
    assert html.count('class="chat-join') == 2
    assert "never DM you first" in html


def test_observatory_link_is_a_button():
    import pathlib
    html = site_source()
    assert '<a class="btn btn-primary obs-cta" href="/observatory">Explore download history and project usage →</a>' in html
    assert ".btn-primary {" in pathlib.Path("static/css/rt.css").read_text()


def test_about_order_and_community_links():
    import pathlib
    from requeststoken.config import PRIMARY_LINKS
    html = pathlib.Path("templates/about.html").read_text()
    order = ['<h3 id="press">Written about Kenneth</h3>', '<h3 id="talks">Talks</h3>', "<h3 id=\"reading\">Other interesting reading</h3>", "<h3 id=\"family\">Family history</h3>", "<h3 id=\"genome\">Open-source genome data</h3>", 'class="fine">Source:']
    idx = [html.index(h) for h in order]
    assert idx == sorted(idx)
    assert html.index('id="talks"') < html.index("skYBOXE02OQ") < html.index("bpZS9ehw98k") < html.index('id="reading"')
    assert html.index('id="writing"') < html.index('id="press"') < html.index('id="family"')
    assert "<h3>Lineage</h3>" not in html
    assert '("community", "/community", "Community")' in site_source()
    hrefs = [l["href"] for l in PRIMARY_LINKS]
    assert "https://t.me/+dz8vzHYMS78xMzA1" in hrefs
    assert "https://x.com/i/chat/group_join/g2113555281518977367/54IJSMh16G" in hrefs


def test_other_projects_list():
    import pathlib
    html = site_source()
    block = pathlib.Path("templates/projects.html").read_text()
    assert "Other projects by Kenneth" in block
    for href in ("https://pytheory.org", "https://interpretations.kennethreitz.org", "https://kjvstudy.org", "https://kennethreitz.org", "https://github.com/kennethreitz"):
        assert f'href="{href}"' in block
    assert 'href="/projects"' in pathlib.Path("templates/about.html").read_text()
    assert "short-version" not in html


def test_other_projects_source_links():
    import pathlib
    html = site_source()
    block = pathlib.Path("templates/projects.html").read_text()
    assert "Other projects by Kenneth" in block
    assert "All open source: each project’s source is public on GitHub. Licenses vary by project." in block
    assert 'href="https://github.com/kennethreitz" target="_blank"' in block
    assert "context.kennethreitz.org" not in block and "kennethreitz/context" not in block
    for r in ("pytheory", "interpretations", "kjvstudy.org", "kennethreitz.org"):
        assert f'href="https://github.com/kennethreitz/{r}" target="_blank" rel="noopener noreferrer">source</a>' in block


def test_faq_help_safety_updates_origin():
    import pathlib
    html = site_source()
    for name in ("home", "faq", "library", "updates", "safety", "community"):
        assert pathlib.Path(f"templates/{name}.html").exists()
    assert "Is there an official $requests X account?" in html
    assert "is the project’s account" in html and "Kenneth’s personal account" in html
    assert "@RequestsToken" in html and "are not official" in html
    assert "no underscore" in html and "requeststoken.com" in html
    assert "We’re planning to launch an official X account soon." not in html
    assert "There isn’t one today." not in html
    assert "Watch this site for the announcement." not in html
    assert "x.com/requeststoken" not in html.lower()
    assert "listed on this page first" not in html
    assert "What’s the utility?" in html and "no web3 features are planned" in html
    assert "requests.py lived inside that project until he split it into its own repository in 2011" in html
    assert html.index('tl-year">Origin') < html.index('tl-year">2011')
    assert "How you can help" in html and "no raids, and no paid promoters" in html
    assert "Real DexScreener profile updates are paid directly to DexScreener." in html
    assert "bot or admin access" in html
    dates = [d for d in ("2026-10-21", "2026-10-24", "2026-10-25", "2026-10-27", "2026-10-28", "2026-10-29")]
    idx = [html.index(f'<time datetime="{d}">') for d in dates]
    assert idx == sorted(idx, reverse=True)
    assert "his wife needs a new car" in html and "short-version" not in html
    assert '("faq", "/faq", "FAQ")' in html


def test_faq_whats_next():
    import pathlib
    html = site_source()
    assert "What’s next?" in html and "One day at a time." in html
    faq = pathlib.Path("templates/faq.html").read_text()
    assert faq.index("What’s next?") < faq.index('href="/updates"')


def test_telegram_rules_psf_fellow_safety():
    import pathlib
    html = site_source()
    assert "The Telegram group is locked, so members can’t chat there." in html
    assert '<time datetime="2026-10-30">' in html
    assert "report spam or FUD to the moderators rather than arguing" in html
    assert "Fellow of the Python Software Foundation (PSF) and served one year on its board" in html
    assert "nobody official promotes other tokens" in html


def test_every_page_renders_with_nav_and_footer():
    from site_pages import PAGES
    api, _client = build({})
    for path in PAGES:
        response = api.requests.get(path)
        assert response.status_code == 200, path
        html = response.text
        assert 'class="mainnav"' in html and 'id="links"' in html and CONTRACT in html
        assert "not financial advice" in html.lower()
        assert 'href="https://x.com/Requests_Token"' in html
        assert 'href="https://x.com/kennethreitz42"' in html
        assert "Kenneth’s personal account" in html
        assert ' style="' not in html
        if path != "/":
            assert f'href="{path}" aria-current="page"' in html
            assert "site.js" not in html


def test_swirl_logo_removed():
    import pathlib
    for f in pathlib.Path("templates").glob("*.html"):
        text = f.read_text()
        assert "M46 11c9" not in text and 'cx="49" cy="12"' not in text, f


def test_favicon_is_served_for_every_browser():
    import struct

    api, _client = build({})
    for path in ("/", "/observatory", "/api"):
        html = api.requests.get(path).text
        assert 'rel="icon" href="/favicon.ico?v=' in html, path
        assert 'name="twitter:site" content="@Requests_Token"' in html, path
        assert 'name="twitter:creator" content="@kennethreitz42"' in html, path
        if path != "/api":
            assert '"sameAs":["https://x.com/Requests_Token","https://x.com/kennethreitz42"]' in html, path
        else:
            assert "<script" not in html, path
        assert 'href="/static/favicon.svg?v=' in html and 'type="image/svg+xml"' in html, path
        assert 'href="/static/favicon-32.png?v=' in html and 'sizes="32x32"' in html, path
        assert 'rel="apple-touch-icon" href="/apple-touch-icon.png?v=' in html, path

    ico = api.requests.get("/favicon.ico")
    assert ico.status_code == 200
    assert ico.headers["content-type"].split(";")[0] == "image/x-icon"
    assert ico.content[:4] == b"\x00\x00\x01\x00"
    assert struct.unpack_from("<H", ico.content, 4)[0] == 3
    assert ico.headers["cache-control"] == "no-cache"
    assert b"M22.5 6.5" not in ico.content

    for path, size in (("/apple-touch-icon.png", 180), ("/apple-touch-icon-precomposed.png", 180), ("/static/favicon-32.png", 32)):
        png = api.requests.get(path)
        assert png.status_code == 200, path
        assert png.headers["content-type"].split(";")[0] == "image/png", path
        assert png.content[:8] == b"\x89PNG\r\n\x1a\n"
        assert struct.unpack(">II", png.content[16:24]) == (size, size), path

    svg = api.requests.get("/static/favicon.svg")
    assert svg.status_code == 200
    assert svg.headers["content-type"].split(";")[0] == "image/svg+xml"
    assert b'width="32"' in svg.content and b'height="32"' in svg.content
    assert b"#e4c36a" in svg.content and b"#0f1311" in svg.content
    assert b"M22.5 6.5" not in svg.content
    assert svg.headers["cache-control"] == "no-cache"


def test_classic_projects_listed():
    import pathlib
    block = pathlib.Path("templates/projects.html").read_text()
    for repo in ("kennethreitz/tablib", "kennethreitz/maya", "kennethreitz/records", "postmanlabs/httpbin", "psf/requests-html",
                 "kennethreitz/python-guide", "kennethreitz/dj-database-url", "kennethreitz/osx-gcc-installer"):
        assert f"https://github.com/{repo}" in block
