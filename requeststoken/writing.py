"""Latest essays from kennethreitz.org, read from its RSS feed server-side.

Feed content is untrusted: only title, link, date and a plain-text excerpt are
kept, links must be https://kennethreitz.org/..., and no feed HTML is ever
rendered. Results are cached (Redis when configured, memory otherwise) with a
last-good copy so the homepage never depends on the blog being up.
"""

from __future__ import annotations

import html
import logging
import re
import threading
import time
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit

import httpx
import orjson
from defusedxml import ElementTree

log = logging.getLogger(__name__)

FEED_URL = "https://kennethreitz.org/feed.xml"
MORE_URL = "https://kennethreitz.org/essays"
PINNED_URL = "https://kennethreitz.org/essays/2026-10-25-about_requests_the_token"
PINNED_FALLBACK = {"title": "About $requests, the Token", "link": PINNED_URL, "date": "2026-10-25",
                   "excerpt": "There’s a memecoin named after Requests, and I’m involved with it. I’d rather you hear about it from me, plainly, than from a screenshot."}
FRESH_TTL = 45 * 60
RETRY_AFTER = 5 * 60
LIMIT = 5
PREFIX = "requeststoken:writing:v1"

_TAGS = re.compile(r"<[^>]*>")
_SPACE = re.compile(r"\s+")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def plain(value: str | None, limit: int) -> str:
    """Strip markup and entities to plain text and cap its length at a word boundary."""
    text = html.unescape(_TAGS.sub(" ", html.unescape(value or "")))
    text = _SPACE.sub(" ", _CONTROL.sub("", _TAGS.sub(" ", text))).strip().lstrip("> ").strip()
    if len(text) <= limit and not text.endswith("..."):
        return text
    text = text.removesuffix("...").rstrip()
    if len(text) > limit:
        text = text[:limit].rsplit(" ", 1)[0]
    return text.rstrip(" ,;:.—-") + "…"


def safe_link(value: str | None) -> str | None:
    link = (value or "").strip()
    parts = urlsplit(link)
    if parts.scheme != "https" or parts.hostname != "kennethreitz.org" or parts.username or parts.password or parts.port:
        return None
    return link


def parse_feed(payload: bytes) -> list[dict]:
    root = ElementTree.fromstring(payload)
    posts = []
    def text(item, tag):
        node = item.find(tag)
        return None if node is None else "".join(node.itertext())

    for item in root.iter("item"):
        link = safe_link(text(item, "link"))
        title = plain(text(item, "title"), 140)
        if not link or not title:
            continue
        try:
            date = parsedate_to_datetime((text(item, "pubDate") or "").strip()).date().isoformat()
        except (TypeError, ValueError):
            date = None
        posts.append({"title": title, "link": link, "date": date, "excerpt": plain(text(item, "description"), 200)})
    return posts


def arrange(posts: list[dict]) -> list[dict]:
    """Pinned token essay first, then the newest others, LIMIT in total."""
    pinned = next((p for p in posts if p["link"] == PINNED_URL), PINNED_FALLBACK)
    rest = [p for p in posts if p["link"] != PINNED_URL]
    return [{**pinned, "pinned": True}] + [{**p, "pinned": False} for p in rest[: LIMIT - 1]]


class Writing:
    def __init__(self, client=None, fetch=None, clock=time.time):
        self.client = client  # optional redis client (decode_responses=True)
        self.fetch = fetch or self._fetch
        self.clock = clock
        self._lock = threading.Lock()
        self._memory: dict | None = None  # {"posts", "fetchedAt"}
        self._fresh_until = 0.0
        self._retry_at = 0.0

    @staticmethod
    def _fetch() -> bytes:
        response = httpx.get(FEED_URL, timeout=3.0, follow_redirects=False,
                             headers={"User-Agent": "requeststoken.org (+https://requeststoken.org)"})
        response.raise_for_status()
        return response.content[:2_000_000]

    def _redis(self, op, *args):
        if self.client is None:
            return None
        try:
            return getattr(self.client, op)(*args)
        except Exception as exc:  # never log connection details
            log.warning("writing cache %s failed: %s", op, type(exc).__name__)
            return None

    def _last_good(self):
        raw = self._redis("get", f"{PREFIX}:last")
        if raw:
            try:
                return orjson.loads(raw)
            except orjson.JSONDecodeError:
                pass
        return self._memory

    def read(self) -> dict:
        now = self.clock()
        with self._lock:
            fresh = self._redis("exists", f"{PREFIX}:fresh")
            if fresh or now < self._fresh_until or now < self._retry_at:
                cached = self._last_good()
                if cached:
                    return self._envelope(cached, "cached")
            try:
                posts = parse_feed(self.fetch())
                if not posts:
                    raise ValueError("empty feed")
            except Exception as exc:
                log.warning("writing feed unavailable: %s", type(exc).__name__)
                self._retry_at = now + RETRY_AFTER
                cached = self._last_good()
                return self._envelope(cached, "stale") if cached else self._envelope({"posts": [], "fetchedAt": None}, "unavailable")
            value = {"posts": posts, "fetchedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now))}
            self._memory, self._fresh_until, self._retry_at = value, now + FRESH_TTL, 0.0
            self._redis("set", f"{PREFIX}:last", orjson.dumps(value).decode())
            self._redis("setex", f"{PREFIX}:fresh", FRESH_TTL, "1")
            return self._envelope(value, "current")

    @staticmethod
    def _envelope(value: dict, status: str) -> dict:
        return {"status": status, "source": FEED_URL, "more": MORE_URL,
                "fetchedAt": value.get("fetchedAt"), "posts": arrange(value.get("posts") or [])}
