"""Constants for the requests token site. Facts live here once."""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DISK_CACHE = ROOT / "var" / "upstream-cache.json"

CONTRACT = "6ceuPfcE67PLs3Z6CbJpFpqNyAevVocP2zsDkbM1pump"
PACKAGE = "requests"

DEX_API = f"https://api.dexscreener.com/latest/dex/tokens/{CONTRACT}"
DEX_PAGE = f"https://dexscreener.com/solana/{CONTRACT}"
PUMP_PAGE = f"https://pump.fun/coin/{CONTRACT}"
FOMO_PAGE = f"https://fomo.family/tokens/solana/{CONTRACT}"
# Public join link for the X group DM. Kenneth published this; it is the only group chat.
CHAT_JOIN = "https://x.com/i/chat/group_join/g2113555281518977367/54IJSMh16G"


PYPI_PAGE = f"https://pypi.org/project/{PACKAGE}/"
PYPI_RECENT = f"https://pypistats.org/api/packages/{PACKAGE}/recent"
PYPI_OVERALL = f"https://pypistats.org/api/packages/{PACKAGE}/overall"
PYPISTATS_PAGE = f"https://pypistats.org/packages/{PACKAGE}"

DOCS_PAGE = "https://requests.readthedocs.io/"
SOURCE_PAGE = "https://github.com/psf/requests"

USER_AGENT = "requeststoken.org/1.0 (+https://requeststoken.org)"

# Successful DexScreener reads are reused for a little under the 30s poll.
TOKEN_TTL = 20
TOKEN_ERROR_TTL = 15
# Download totals move on a daily cadence.
STATS_TTL = 15 * 60
STATS_ERROR_TTL = 60

PRIMARY_LINKS = (
    {
        "label": "This website",
        "href": "https://requeststoken.org/",
        "note": "The page you are on",
    },
    {
        "label": "Source on GitHub",
        "href": "https://github.com/kennethreitz/requeststoken.org",
        "note": "The code for this site",
    },
    {
        "label": "DexScreener",
        "href": DEX_PAGE,
        "note": "Market pair for this contract",
    },
    {
        "label": "pump.fun",
        "href": PUMP_PAGE,
        "note": "Token page for this contract",
    },
    {
        "label": "fomo",
        "href": FOMO_PAGE,
        "note": "Trade and follow this contract on fomo",
    },
    {
        "label": "Group chat",
        "href": CHAT_JOIN,
        "note": "The only group chat, on X",
    },
    {
        "label": "Requests on PyPI",
        "href": PYPI_PAGE,
        "note": "The library, not the token",
    },
    {
        "label": "Requests documentation",
        "href": DOCS_PAGE,
        "note": "The library",
    },
    {
        "label": "Requests source",
        "href": SOURCE_PAGE,
        "note": "github.com/psf/requests",
    },
    {
        "label": "pypistats.org",
        "href": PYPISTATS_PAGE,
        "note": "Public PyPI download statistics",
    },
)

# Published on the token's DexScreener profile. Confirm before treating
# them as canonical; they are labeled that way on the page.
PROFILE_LINKS = (
    {
        "label": "Kenneth Reitz on X",
        "href": "https://x.com/kennethreitz42",
        "note": "Published on the token's DexScreener profile",
    },
)

# Accounts known to be fraudulent / not affiliated. Never shown on the site,
# even if they appear on the DexScreener profile.
BLOCKED_HANDLES = ("requeststoken",)


def is_blocked_link(link) -> bool:
    text = " ".join(str(link.get(k, "")) for k in ("label", "href", "url", "handle")).lower()
    return any(h in text.replace("requeststoken.org", "") for h in BLOCKED_HANDLES)


def filter_links(links):
    return tuple(l for l in links if not is_blocked_link(l))


PROFILE_LINKS = filter_links(PROFILE_LINKS)
