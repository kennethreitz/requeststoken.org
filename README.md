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
| `UMAMI_WEBSITE_ID` | Website ID from the Umami dashboard. Unset means the tracker is not rendered. |
| `UMAMI_SCRIPT_URL` | Tracker script URL. Defaults to `https://analytics.kennethreitz.org/script.js`. |

## Analytics

Page views go to the Umami instance already used by kennethreitz.org and Kenneth’s other sites: `https://analytics.kennethreitz.org`. There is no website ID for requeststoken.org in this repo. Create the site in that dashboard, set `UMAMI_WEBSITE_ID` where this app is deployed, and `templates/index.html` renders this in `<head>`:

```html
<script defer src="https://analytics.kennethreitz.org/script.js" data-website-id="…"></script>
```

The tag is omitted when `UMAMI_WEBSITE_ID` is unset or not a UUID. `UMAMI_SCRIPT_URL` replaces the script URL; the content security policy allows that script’s origin only while the tag is rendered. The tracker sends to `/api/send` on the same origin.

## Download stats

`GET /api/requests` reads the PyPI package `requests` from the public, keyless [pypistats.org](https://pypistats.org/packages/requests) API (recent totals, mirrors excluded). No API keys are needed. pypistats does not publish an all-time total, so that figure is left off.

If pypistats fails, the page says download stats are temporarily unavailable, or shows the last saved figures from `var/upstream-cache.json`, marked as such. Reads are cached in memory for 15 minutes.

## Market figures

`GET /api/token` reads DexScreener:

`https://api.dexscreener.com/latest/dex/tokens/6ceuPfcE67PLs3Z6CbJpFpqNyAevVocP2zsDkbM1pump`

The first object in `pairs` is the one displayed. The browser asks `/api/token` again about every 30 seconds. The server reuses a successful read for 20 seconds, and if a later read fails it keeps showing the last good one.

## Tests

```bash
uv run pytest
```

The tests call the JSON routes through Responder’s in-process client. DexScreener and pypistats are replaced with scripted responses, so the suite does not use the network.

## Deploy

The container build uses uv and the lockfile. It does not install packages any other way.

```bash
docker build -t requeststoken .
docker run --rm -p 5042:5042 requeststoken
```

Pass `PORT` if the platform assigns one. The app binds `0.0.0.0` on that port.

`Procfile` is `web: .venv/bin/python app.py`. On a platform that expects a Procfile, install dependencies in the build step with `uv sync --frozen --no-dev`, then start from the Procfile.

The cache is per process and in `var/` on local disk. More than one worker means more than one cache.

## Official website

The official website is requeststoken.org. DexScreener currently lists requeststoken.com, which is not official; that listing will be updated.

## Official account

The only official account is [@kennethreitz42](https://x.com/kennethreitz42) on X. Anyone else claiming to speak for $requests is not official. Other X accounts listed on the token's DexScreener page are filtered out (`BLOCKED_HANDLES` in `requeststoken/config.py`).

There is no roadmap on this page.

## Fonts

Fraunces, Outfit, and IBM Plex Mono are self-hosted Latin subsets under the SIL Open Font License. The license texts are in `static/fonts/`.
