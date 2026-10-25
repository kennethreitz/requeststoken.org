# requeststoken.org

An honest homepage for the **requests** community token on Solana. The coin is named for Kenneth Reitz’s Python library [Requests](https://github.com/psf/requests). Creator fees are directed to him. This site states what is known, marks what is not, and does not take wallets, keys, or payments.

The server is [Responder](https://github.com/kennethreitz/responder).

## Run locally

Install [uv](https://docs.astral.sh/uv/), then from this directory:

```bash
uv sync
uv run python app.py
```

Open http://127.0.0.1:5042

`uv sync` creates `.venv` from `uv.lock`. `uv run` uses that environment. The process listens on `0.0.0.0`.

Optional environment variables, in a `.env` file (loaded if present, without overriding variables you already exported) or in the environment:

| Variable | Purpose |
| --- | --- |
| `PORT` | Port to bind. Defaults to `5042`. |
| `LOG_LEVEL` | Python log level. Defaults to `INFO`. |
| `REDIS_URL` | Shared Redis connection for observatory snapshots, history and refresh coordination. Unset means saved-snapshot review only. |
| `REQUESTS_OBSERVATORY_REDIS_PREFIX` | Namespace shared by all workers; defaults to `requeststoken:observatory:v1`. Use a different value for previews. |
| `REQUESTS_OBSERVATORY_REFRESH` | Set to `0` to read existing snapshots without requesting upstream updates. |

## Download stats

`GET /api/requests` reads the PyPI package `requests` from the public, keyless [pypistats.org](https://pypistats.org/packages/requests) API (recent totals, mirrors excluded). No API keys are needed. pypistats does not publish an all-time total, so that figure is left off.

If pypistats fails, the page says download stats are temporarily unavailable, or shows the last saved figures from `var/upstream-cache.json`, marked as such. Reads are cached in memory for 15 minutes.

## Requests observatory

`/observatory` adds calendar-day/week/month counts, a historical total, dated
history, a chart, CSV export and 120 reviewed public projects with usage details
and pinned source evidence. Historical OpenAI and Anthropic SDK versions appear
in a separate section. The homepage links to the observatory; `/api/requests`
keeps its existing pypistats response and filtering.

`/api/observatory` reads a separate ClickPy/ClickHouse source without API keys.
A saved snapshot renders while one Redis-coordinated background refresh runs.
Set `REDIS_URL` to enable shared refreshes across workers and hosts. Snapshots
and daily source-check history have no expiry; only the 120-second refresh
lease and hourly/ten-minute retry timers expire. Configure Redis persistence,
backups and `noeviction` to retain history across restarts.

Without Redis, the dated audit still renders and no upstream jobs start. A Redis
outage serves the last snapshot held by that process (or the bundled audit),
explicitly marked as a fallback. Set `REQUESTS_OBSERVATORY_REFRESH=0` for offline
review; configured operation uses hourly, visit-triggered refresh. See the
[Redis setup and optional SQLite import](docs/observatory.md#redis-setup-and-durability).

This is a **delayed daily source**, not a realtime event feed. Counts never grow
from clock ticks. Missing data stays unknown/Pending, and the historical total
does not cover the library's entire lifetime. See [source definitions, coverage,
maintenance and deferred realtime work](docs/observatory.md).

The Python suite covers the new routes, source validation, shared cache,
lease expiry, failures, corrections and optional SQLite migration. Redis tests
start an isolated `redis-server` on a random loopback port, with a temporary password;
they never use `REDIS_URL` or a production database. Install `redis-server` on
the test machine (or set `REDIS_SERVER_BINARY`) and require those tests with:

```sh
uv run pytest --require-redis
node --test tests/observatory-stats.test.mjs
```

Without `--require-redis`, Redis integration tests are skipped if the server
binary is absent. Node is needed only for JavaScript tests, not deployment.

## Market figures

`GET /api/token` reads DexScreener:

`https://api.dexscreener.com/latest/dex/tokens/6ceuPfcE67PLs3Z6CbJpFpqNyAevVocP2zsDkbM1pump`

The first object in `pairs` is the one displayed. The browser asks `/api/token` again about every 30 seconds. The server reuses a successful read for 20 seconds, and if a later read fails it keeps showing the last good one.

## Tests

```bash
uv run pytest
```

The tests call the JSON routes through Responder’s in-process client. All HTTP
upstreams are replaced with scripted responses. Redis integration tests use
only their isolated local loopback server; no external services or credentials are needed.

## Deploy

The container build uses uv and the lockfile. It does not install packages any other way.

```bash
docker build -t requeststoken .
docker run --rm -p 5042:5042 requeststoken
```

Pass `PORT` if the platform assigns one. The app binds `0.0.0.0` on that port.

`Procfile` is `web: .venv/bin/python app.py`. On a platform that expects a Procfile, install dependencies in the build step with `uv sync --frozen --no-dev`, then start from the Procfile.

The existing homepage's token/pypistats cache remains per process and in `var/`
on local disk. The observatory uses Redis shared by all workers with the same
URL and namespace. Provision that service and pass `REDIS_URL` to enable its
refreshes; see the [deployment handoff](deploy/README.md#observatory-redis).

## Official website

The official website is requeststoken.org. Since the community takeover on Oct 24, 2026, the DexScreener listing shows only requeststoken.org and @kennethreitz42.

## Official account

The only official account is [@kennethreitz42](https://x.com/kennethreitz42) on X. Anyone else claiming to speak for $requests is not official. Other X accounts listed on the token's DexScreener page are filtered out (`BLOCKED_HANDLES` in `requeststoken/config.py`).

There is no roadmap on this page.

## Fonts

Fraunces, Outfit, and IBM Plex Mono are self-hosted Latin subsets under the SIL Open Font License. The license texts are in `static/fonts/`.
