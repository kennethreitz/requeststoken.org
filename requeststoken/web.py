"""Responder routes for the page and the two JSON endpoints."""

from __future__ import annotations

import json
import logging
import os
import re
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from typing import NamedTuple
from urllib.parse import urlsplit

import responder

from requeststoken.config import (
    CONTRACT,
    PRIMARY_LINKS,
    PROFILE_LINKS,
    PUMP_PAGE,
    DEX_PAGE,
    ROOT,
)
from requeststoken.data import Services, unavailable_stats, unavailable_token

logger = logging.getLogger("requeststoken")

_POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix="upstream")

# Same self-hosted Umami Kenneth uses for kennethreitz.org and the other sites.
DEFAULT_UMAMI_SCRIPT_URL = "https://analytics.kennethreitz.org/script.js"

_WEBSITE_ID = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)
_SCRIPT_HOST = re.compile(r"[A-Za-z0-9.-]+")


class Umami(NamedTuple):
    website_id: str
    script_url: str
    origin: str


def umami_from_env(environ: Mapping[str, str]) -> Umami | None:
    """Tracker settings, or None when the tag should be left out.

    ``UMAMI_WEBSITE_ID`` is the dashboard id for this site. The script URL
    defaults to Kenneth's Umami and can be replaced with ``UMAMI_SCRIPT_URL``.
    A blank or unusable value omits the tag and leaves the content security
    policy unchanged.
    """
    website_id = environ.get("UMAMI_WEBSITE_ID", "").strip()
    if not website_id:
        return None
    if _WEBSITE_ID.fullmatch(website_id) is None:
        logger.warning("UMAMI_WEBSITE_ID is not a UUID; analytics tag omitted")
        return None
    script_url = environ.get("UMAMI_SCRIPT_URL", "").strip() or DEFAULT_UMAMI_SCRIPT_URL
    origin = _script_origin(script_url)
    if origin is None:
        logger.warning("UMAMI_SCRIPT_URL is not an http(s) script; analytics tag omitted")
        return None
    return Umami(website_id, script_url, origin)


def _script_origin(url: str) -> str | None:
    if any(ch.isspace() or ch in "\"'<>\\;" for ch in url):
        return None
    parts = urlsplit(url)
    host = parts.hostname
    if parts.scheme not in {"http", "https"} or host is None:
        return None
    if parts.username or parts.password or not parts.path.endswith(".js"):
        return None
    if _SCRIPT_HOST.fullmatch(host) is None:
        return None
    if parts.port is None:
        return f"{parts.scheme}://{host}"
    return f"{parts.scheme}://{host}:{parts.port}"


def content_security_policy(script_origin: str | None = None) -> str:
    extra = f" {script_origin}" if script_origin else ""
    return (
        "default-src 'self'; "
        "img-src 'self' data:; "
        "style-src 'self'; "
        "font-src 'self'; "
        f"script-src 'self'{extra}; "
        f"connect-src 'self'{extra}; "
        "base-uri 'self'; "
        "form-action 'none'; "
        "frame-ancestors 'none'"
    )


def safe_json(data) -> str:
    text = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return text.replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e")


def create_app(
    services: Services | None = None,
    environ: Mapping[str, str] | None = None,
) -> responder.API:
    services = services or Services()
    settings = os.environ if environ is None else environ
    umami = umami_from_env(settings)
    policy = content_security_policy(umami.origin if umami else None)
    api = responder.API(
        static_dir=str(ROOT / "static"),
        static_route="/static",
        templates_dir=str(ROOT / "templates"),
        sessions=False,
        security_headers={"content_security_policy": policy},
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
            primary_links=PRIMARY_LINKS,
            profile_links=PROFILE_LINKS,
            umami_website_id=umami.website_id if umami else "",
            umami_script_url=umami.script_url if umami else "",
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
