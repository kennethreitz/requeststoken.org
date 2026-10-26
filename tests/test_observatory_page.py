from starlette.testclient import TestClient

from requeststoken.web import create_app, recent_counts


def test_recent_counts_use_reported_rows_only():
    snap = {"latestDate": "2026-10-25", "daily": [
        {"date": "2026-10-19", "downloads": 1}, {"date": "2026-10-21", "downloads": 2},
        {"date": "2026-10-25", "downloads": 4}, {"date": "2026-10-18", "downloads": 100}]}
    got = recent_counts(snap)
    assert got["day"] == {"date": "2026-10-25", "downloads": 4}
    assert got["week"] == {"start": "2026-10-19", "end": "2026-10-25", "downloads": 7, "available": 3}
    assert recent_counts({"latestDate": "2026-10-25", "daily": []}) == {"day": None, "week": None}


def test_page_renders_recent_cards_and_versioned_assets(monkeypatch):
    monkeypatch.delenv("REDIS_URL", raising=False)
    client = TestClient(create_app())
    html = client.get("/observatory").text
    assert "Latest day" in html and "Last 7 days" in html
    assert "latest reported day" in html
    assert "/static/js/observatory.mjs?v=" in html
    assert client.get("/static/js/observatory.mjs").headers["cache-control"] == "no-cache"
