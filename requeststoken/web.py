"""Responder routes for the page and the two JSON endpoints."""

from __future__ import annotations

import hashlib
import json
from datetime import date, timedelta
import logging
from concurrent.futures import ThreadPoolExecutor

import responder

from requeststoken.config import (
    CONTRACT,
    PRIMARY_LINKS,
    PROFILE_LINKS,
    PUMP_PAGE,
    FOMO_PAGE,
    DEX_PAGE,
    ROOT,
)
from requeststoken.data import Services, unavailable_stats, unavailable_token
from requeststoken.observatory import DownloadObservatory, adoption_manifest

logger = logging.getLogger("requeststoken")

_POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix="upstream")

_CSP = (
    "default-src 'self'; "
    "img-src 'self' data:; "
    "style-src 'self'; "
    "font-src 'self'; "
    "script-src 'self' https://analytics.kennethreitz.org; "
    "connect-src 'self' https://analytics.kennethreitz.org; "
    "base-uri 'self'; "
    "form-action 'none'; "
    "frame-ancestors 'none'"
)


def safe_json(data) -> str:
    text = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return text.replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e")


class RevalidateAssets:
    """Make browsers revalidate JS and CSS so a deploy never mixes old scripts with new pages."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "") if scope.get("type") == "http" else ""
        if not path.startswith("/static/") or not path.endswith((".js", ".mjs", ".css")):
            return await self.app(scope, receive, send)

        async def wrapped(message):
            if message["type"] == "http.response.start":
                headers = [(k, v) for k, v in message.get("headers", []) if k.lower() != b"cache-control"]
                headers.append((b"cache-control", b"no-cache"))
                message = {**message, "headers": headers}
            await send(message)

        return await self.app(scope, receive, wrapped)


def _asset_version() -> str:
    digest = hashlib.sha256()
    for path in sorted((ROOT / "static" / "css").glob("*.css")) + sorted((ROOT / "static" / "js").glob("*.mjs")):
        digest.update(path.read_bytes())
    return digest.hexdigest()[:10]


def recent_counts(snapshot: dict) -> dict:
    """Latest reported day and the 7 days ending on it, from reported rows only."""
    rows = {row["date"]: row["downloads"] for row in snapshot.get("daily", [])}
    latest = snapshot.get("latestDate")
    if latest not in rows:
        return {"day": None, "week": None}
    end = date.fromisoformat(latest)
    days = [(end - timedelta(days=i)).isoformat() for i in range(7)]
    present = [rows[d] for d in days if d in rows]
    return {
        "day": {"date": latest, "downloads": rows[latest]},
        "week": {"start": days[-1], "end": latest, "downloads": sum(present), "available": len(present)},
    }


def create_app(services: Services | None = None, observatory: DownloadObservatory | None = None) -> responder.API:
    services = services or Services()
    observatory = observatory or DownloadObservatory()
    api = responder.API(
        static_dir=str(ROOT / "static"),
        static_route="/static",
        templates_dir=str(ROOT / "templates"),
        sessions=False,
        security_headers={"content_security_policy": _CSP},
    )
    api.add_middleware(RevalidateAssets)
    asset_version = _asset_version()

    @api.route("/")
    def home(req, resp):
        token_future = _POOL.submit(
            _section,
            services.token,
            unavailable_token,
            "Market data is temporarily unavailable.",
        )
        stats_future = _POOL.submit(
            _section,
            services.stats,
            unavailable_stats,
            "Download stats are temporarily unavailable.",
        )
        token = token_future.result()
        stats = stats_future.result()
        resp.headers["Cache-Control"] = "no-store"
        resp.render(
            "index.html",
            token=token,
            stats=stats,
            stats_json=safe_json(stats),
            contract=CONTRACT,
            dex_page=DEX_PAGE,
            pump_page=PUMP_PAGE,
            fomo_page=FOMO_PAGE,
            primary_links=PRIMARY_LINKS,
            profile_links=PROFILE_LINKS,
        )

    @api.route("/api/token")
    def token(req, resp):
        resp.headers["Cache-Control"] = "public, max-age=15"
        resp.media = _section(
            services.token,
            unavailable_token,
            "Market data is temporarily unavailable.",
        )

    @api.route("/api/requests")
    def requests_stats(req, resp):
        resp.headers["Cache-Control"] = "public, max-age=300"
        resp.media = _section(
            services.stats,
            unavailable_stats,
            "Download stats are temporarily unavailable.",
        )

    @api.route("/observatory")
    def observatory_page(req, resp):
        downloads = observatory.read()
        adoption = adoption_manifest()
        resp.headers["Cache-Control"] = "no-store"
        resp.render(
            "observatory.html", downloads=downloads, adoption=adoption,
            recent=recent_counts(downloads["snapshot"]), asset_version=asset_version,
            downloads_json=safe_json(downloads), adoption_json=safe_json(adoption),
            categories=sorted({entry["category"] for entry in adoption["entries"]}),
            organization_count=len({entry["organization"] for entry in adoption["entries"]}),
        )

    @api.route("/api/observatory")
    def observatory_stats(req, resp):
        resp.headers["Cache-Control"] = "no-store"
        resp.media = observatory.read()

    return api


def _section(loader, fallback, detail="Market data is temporarily unavailable."):
    try:
        return loader()
    except Exception:
        logger.exception("section failed")
        return fallback(detail)
