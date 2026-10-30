from site_pages import site_source, site_html, TEMPLATES
from requeststoken.config import PROFILE_LINKS, filter_links


def test_blocked_handle_filtered_case_insensitive():
    links = [{"label": "x", "href": "https://x.com/RequestsToken"},
             {"label": "@REQUESTSTOKEN", "href": "https://x.com/foo"},
             {"label": "ok", "href": "https://x.com/kennethreitz42"}]
    assert [l["label"] for l in filter_links(links)] == ["ok"]
    assert not any("requeststoken" in l["href"].lower() for l in PROFILE_LINKS)


def test_rendered_page_has_no_blocked_account():
    import pathlib
    html = site_source().lower()
    assert "x.com/requeststoken" not in html


def test_dexscreener_note():
    from requeststoken.config import PRIMARY_LINKS
    dex = [l for l in PRIMARY_LINKS if l["label"] == "DexScreener"][0]
    assert dex["note"] == "Market pair for this contract"


def test_no_pepy_or_chart():
    import pathlib
    for f in [*[f"templates/{n}.html" for n in TEMPLATES], "static/js/site.js", "requeststoken/data.py", "requeststoken/config.py", "README.md", "Dockerfile", ".env.example"]:
        t = pathlib.Path(f).read_text().lower()
        assert "pepy" not in t, f
    assert 'id="chart"' not in site_source()



def test_footer_links_source_on_github():
    from requeststoken.config import PRIMARY_LINKS
    assert {"label": "Source on GitHub", "href": "https://github.com/kennethreitz/requeststoken.org",
            "note": "The code for this site"} in PRIMARY_LINKS


def test_lock_proof_links_on_chain():
    import pathlib
    html = site_source()
    assert "https://solscan.io/account/BaCrGRdrxHrovQjEK8D4PmjWPVCbeaZUPRKvPJ4WW5vo" in html
    assert "https://solscan.io/tx/2gVXJKnhJ4gXDjPN15CCTXqyDwMkrtz9dBWQByYCxnsHG393QzSYiWjbRLAm7qCsgwbD66i1pk9mfJMfGC6MvoMa" in html
    assert "A locker address or transaction" not in html


def test_creator_fee_disclosure():
    import pathlib
    html = site_source()
    assert "buying more $requests" in html
    assert "not a promise of buybacks, price support, or returns" in html
    assert "A plain description of how creator fees move" not in html


def test_official_site_statement_no_com_link():
    import pathlib
    html = site_source()
    assert "The official website is requeststoken.org." in html
    assert "https://requeststoken.com" not in html
    assert 'href="https://requeststoken.com' not in html


def test_hero_heading_is_dollar_requests():
    import pathlib
    html = site_source()
    assert '<h1><span class="dollar">$</span>requests</h1>' in html
    import re
    assert re.sub(r"<[^>]+>", "", re.search(r"<h1>.*?</h1>", html).group(0)) == "$requests"


def test_official_account_and_no_todos():
    import pathlib
    html = site_source()
    assert 'href="https://x.com/kennethreitz42"' in html
    assert "Anyone else claiming to speak for $requests is not official." in html
    assert "TODO" not in html


def test_dexscreener_links_top_and_footer():
    import pathlib
    from requeststoken.config import DEX_PAGE, PRIMARY_LINKS
    html = pathlib.Path("templates/home.html").read_text()
    top = html[: html.index('id="intentions"')]
    assert 'href="{{ dex_page }}"' in top
    dex = [l for l in PRIMARY_LINKS if l["label"] == "DexScreener"]
    assert dex and dex[0]["href"] == DEX_PAGE
    assert dex[0]["note"] == "Market pair for this contract"


def test_hero_logo_is_local():
    import pathlib
    html = site_source()
    assert 'src="/static/img/requests-caduceus.webp"' in html
    assert "readthedocs.io/en/latest/_static" not in html
    assert pathlib.Path("static/img/requests-caduceus.webp").stat().st_size > 1000


def test_umami_analytics_script_and_csp():
    import pathlib
    html = site_source()
    assert '<script defer src="https://analytics.kennethreitz.org/script.js" data-website-id="b3e5361e-b5d5-4097-95a6-d9d9def5378a"></script>' in html
    from requeststoken.web import _CSP
    assert "script-src 'self' https://analytics.kennethreitz.org" in _CSP
    assert "connect-src 'self' https://analytics.kennethreitz.org" in _CSP
