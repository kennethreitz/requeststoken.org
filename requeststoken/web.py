"""Responder routes for the page and the two JSON endpoints."""

from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor

import responder

from requeststoken.config import (
    CONTRACT,
    PRIMARY_LINKS,
    PROFILE_LINKS,
    PUMP_PAGE,
    FOMO_PAGE,
    CHAT_JOIN,
    DEX_PAGE,
    ROOT,
)
from requeststoken.data import Services, unavailable_stats, unavailable_token

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


def create_app(services: Services | None = None) -> responder.API:
    services = services or Services()
    api = responder.API(
        static_dir=str(ROOT / "static"),
        static_route="/static",
        templates_dir=str(ROOT / "templates"),
        sessions=False,
        security_headers={"content_security_policy": _CSP},
    )

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
            chat_join=CHAT_JOIN,
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

    return api


def _section(loader, fallback, detail="Market data is temporarily unavailable."):
    try:
        return loader()
    except Exception:
        logger.exception("section failed")
        return fallback(detail)
