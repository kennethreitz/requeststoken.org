"""OpenAPI descriptions for the public JSON endpoints.

Shapes mirror what the handlers actually return (see data.py and observatory.py).
"""

TITLE = "requeststoken.org public API"
VERSION = "1.0.0"
DESCRIPTION = (
    "Read-only JSON used by requeststoken.org: token market data relayed from "
    "DexScreener, Requests download figures, and the download observatory. "
    "Figures are informational, may be delayed or unavailable, and are not "
    "financial advice or a promise of any return. No authentication; please "
    "cache responses and poll gently. JSON endpoints negotiate on the Accept "
    "header: JSON by default, YAML when YAML is preferred (application/yaml, "
    "application/x-yaml or text/yaml); responses carry Vary: Accept. Errors are "
    "application/problem+json. Operational routes: /health (readiness, 200 or "
    "503) and /metrics (Prometheus text)."
)

S, N, I, B = {"type": "string"}, {"type": "number"}, {"type": "integer"}, {"type": "boolean"}


def _nullable(schema):
    return {**schema, "type": [schema["type"], "null"]}


def _ok(description, schema):
    return {200: {"description": description, "content": {"application/json": {"schema": schema}, "application/yaml": {"schema": schema}}}}


_TOKEN_STR = [
    "source", "source_label", "name", "symbol", "contract", "price_native_symbol",
    "change_direction", "price_display", "price_native_display", "market_cap_display",
    "change_display", "liquidity_display", "volume_display", "buys_display",
    "sells_display", "activity_display", "status_display",
]
_TOKEN_NULLABLE_STR = [
    "chain", "chain_display", "dex", "dex_display", "price_usd", "price_native",
    "pair_url", "pair_address", "listed_on", "detail", "fetched_at", "fetched_at_display",
]
_TOKEN_NULLABLE_NUM = ["market_cap_usd", "change_h24_pct", "liquidity_usd", "volume_h24_usd"]

TOKEN = {
    "type": "object",
    "properties": {
        "available": {**B, "description": "False when market data could not be fetched; display fields then hold a dash."},
        "stale": {**B, "description": "True when a previously fetched value is being reused."},
        **{k: S for k in _TOKEN_STR},
        **{k: _nullable(S) for k in _TOKEN_NULLABLE_STR},
        **{k: _nullable(N) for k in _TOKEN_NULLABLE_NUM},
        "buys_h24": _nullable(I),
        "sells_h24": _nullable(I),
    },
    "required": ["available", "source", "contract"],
}

DAY = {"type": "object", "properties": {"date": {**S, "format": "date"}, "downloads": I}, "required": ["date", "downloads"]}

REQUESTS = {
    "type": "object",
    "properties": {
        "available": B, "stale": B, "package": S, "source": S, "source_label": S, "source_url": S,
        "total_downloads": _nullable(I),
        "figures": {"type": "array", "items": {"type": "object", "properties": {"label": S, "downloads": I, "display": S, "note": S}}},
        "series": {"type": "array", "items": DAY},
        "series_caption": S, "detail": _nullable(S), "fetched_at": _nullable(S),
    },
}

OBSERVATORY = {
    "type": "object",
    "properties": {
        "snapshot": {
            "type": "object",
            "description": "Retained daily download snapshot for the Requests package (UTC dates).",
            "properties": {
                "package": S, "firstDate": {**S, "format": "date"}, "latestDate": {**S, "format": "date"},
                "rawTotal": {**I, "description": "Sum of all reported daily downloads."},
                "rawLatest": I, "fetchedAt": {**S, "format": "date-time"},
                "daily": {"type": "array", "items": DAY},
                "source": S,
                "queries": {"type": "object", "description": "Source queries used to build the snapshot.", "additionalProperties": S},
            },
        },
        "checkedAt": {**S, "format": "date-time"},
        "status": {"type": "string", "enum": ["current", "cached", "fallback"]},
        "serverTime": {**S, "format": "date-time"},
        "refreshing": B,
        "message": _nullable(S),
    },
    "required": ["snapshot", "checkedAt", "status"],
}

POST = {"type": "object", "properties": {
    "title": S, "link": {**S, "format": "uri", "description": "Always an https://kennethreitz.org/ URL."},
    "date": _nullable({**S, "format": "date"}), "excerpt": {**S, "description": "Plain text, no HTML."},
    "pinned": {**B, "description": "True for the essay about the token, always listed first."}}}

WRITING = {"type": "object", "properties": {
    "status": {"type": "string", "enum": ["current", "cached", "stale", "unavailable"]},
    "source": S, "more": S, "fetchedAt": _nullable({**S, "format": "date-time"}),
    "posts": {"type": "array", "items": POST}}, "required": ["status", "posts"]}

ROUTES = {
    "/api/writing": dict(
        summary="Latest essays from kennethreitz.org", tags=["Writing"],
        description="Up to five posts from https://kennethreitz.org/feed.xml (titles, links, dates and plain-text excerpts only), with the essay about the token pinned first. The feed is cached for 45 minutes and the last good copy is served if the blog is unreachable.",
        responses=_ok("Latest posts", WRITING),
    ),
    "/api/token": dict(
        summary="Token market data", tags=["Token"],
        description="Price, market cap, liquidity, 24h volume and trade counts for the token pair, relayed from DexScreener. Cached for 15 seconds.",
        responses=_ok("Market data, or an `available: false` placeholder", TOKEN),
    ),
    "/api/requests": dict(
        summary="Requests download figures", tags=["Downloads"],
        description="Headline download figures and a recent daily series for the Requests package. Cached for 5 minutes.",
        responses=_ok("Download figures", REQUESTS),
    ),
    "/api/observatory": dict(
        summary="Download observatory snapshot", tags=["Downloads"],
        description="The full daily download history behind /observatory, with its freshness status. Not cached.",
        responses=_ok("Observatory snapshot", OBSERVATORY),
    ),
}
