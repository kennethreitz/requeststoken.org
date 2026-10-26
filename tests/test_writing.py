from starlette.testclient import TestClient

from requeststoken.web import create_app
from requeststoken.writing import PINNED_URL, Writing, parse_feed, plain, safe_link

FEED = b"""<?xml version="1.0"?>
<rss version="2.0"><channel><title>Kenneth Reitz</title>
<item><title>About $requests, the Token</title><link>https://kennethreitz.org/essays/2026-10-25-about_requests_the_token</link>
<pubDate>Sun, 25 Oct 2026 00:00:00 </pubDate><description>There&#x27;s a memecoin named after Requests...</description></item>
<item><title>&lt;script&gt;alert(1)&lt;/script&gt;Hello &amp; <b>world</b></title><link>https://kennethreitz.org/essays/hello</link>
<pubDate>Tue, 20 Oct 2026 00:00:00 </pubDate><description>&lt;p onclick="x()"&gt;Plain &lt;a href="javascript:alert(1)"&gt;text&lt;/a&gt;&lt;/p&gt;</description></item>
<item><title>Evil</title><link>javascript:alert(1)</link><description>x</description></item>
<item><title>Elsewhere</title><link>https://evil.example/kennethreitz.org</link></item>
<item><title>Plain http</title><link>http://kennethreitz.org/essays/x</link></item>
<item><title>Lookalike</title><link>https://kennethreitz.org.evil.example/x</link></item>
<item><title>No date</title><link>https://kennethreitz.org/essays/nodate</link><pubDate>soon</pubDate></item>
</channel></rss>"""


def test_parse_keeps_only_safe_plain_fields():
    posts = parse_feed(FEED)
    assert [p["link"] for p in posts] == [PINNED_URL, "https://kennethreitz.org/essays/hello", "https://kennethreitz.org/essays/nodate"]
    assert posts[0] == {"title": "About $requests, the Token", "link": PINNED_URL, "date": "2026-10-25",
                        "excerpt": "There's a memecoin named after Requests…"}
    assert "<" not in posts[1]["title"] and "<" not in posts[1]["excerpt"]
    assert posts[1]["title"] == "alert(1) Hello & world" and posts[1]["excerpt"] == "Plain text"
    assert posts[2]["date"] is None


def test_parser_rejects_entity_expansion():
    bomb = b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa"><!ENTITY b "&a;&a;">]><rss><channel><item><title>&b;</title></item></channel></rss>'
    try:
        parse_feed(bomb)
    except Exception as exc:
        assert "Forbidden" in type(exc).__name__ or "Entities" in type(exc).__name__
    else:
        raise AssertionError("entity declarations must be refused")


def test_links_and_excerpt_limits():
    assert safe_link("https://kennethreitz.org/essays") == "https://kennethreitz.org/essays"
    for bad in ("http://kennethreitz.org/", "https://user@kennethreitz.org/", "https://kennethreitz.org:444/", "//kennethreitz.org/", ""):
        assert safe_link(bad) is None
    text = plain("word " * 100, 50)
    assert len(text) <= 51 and text.endswith("…")


def test_cache_then_last_good_fallback_then_unavailable():
    clock = [1000.0]
    calls = []
    payloads = [FEED]

    def fetch():
        calls.append(1)
        if not payloads:
            raise OSError("down")
        return payloads.pop()

    writing = Writing(fetch=fetch, clock=lambda: clock[0])
    first = writing.read()
    assert first["status"] == "current" and first["posts"][0]["pinned"] and len(first["posts"]) == 3
    assert writing.read()["status"] == "cached" and len(calls) == 1
    clock[0] += 46 * 60
    stale = writing.read()
    assert stale["status"] == "stale" and stale["posts"] == first["posts"] and len(calls) == 2
    assert writing.read()["status"] == "cached" and len(calls) == 2  # retry backoff

    empty = Writing(fetch=lambda: (_ for _ in ()).throw(OSError("down")))
    down = empty.read()
    assert down["status"] == "unavailable" and down["posts"][0]["link"] == PINNED_URL


def test_homepage_and_api_render_writing_and_survive_outage():
    client = TestClient(create_app(writing=Writing(fetch=lambda: FEED)))
    html = client.get("/").text
    assert 'id="writing"' in html and "Hello &amp; world" in html and "<script>alert" not in html
    assert "javascript:" not in html and "evil.example" not in html
    assert client.get("/api/writing").json()["posts"][0]["link"] == PINNED_URL
    assert "/api/writing" in client.get("/openapi.json").json()["paths"]

    offline = TestClient(create_app())
    page = offline.get("/")
    assert page.status_code == 200 and PINNED_URL in page.text and "More essays" in page.text
