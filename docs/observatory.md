# Requests observatory

The observatory is a library download-history and public-project evidence page.
It runs in the existing Responder application. Shared updates use Redis and the
locked `redis-py` dependency; no download-source API key, Google Cloud account
or frontend build step is required. Without Redis it serves a saved audit.
Realtime event
collection remains deferred; `requests-intraday-proof.sql` is an unexecuted
candidate query for that separate work.

## Routes and files

| Path | Purpose |
| --- | --- |
| `/observatory` | Server-rendered page with interactive counters, archive and project directory |
| `/api/observatory` | Shared source snapshot, cache status and timestamps |
| `requeststoken/observatory.py` | Source queries/validation, background refresh and fallback behavior |
| `requeststoken/observatory_store.py` | Redis snapshot, atomic refresh lease, backoff and source-check history |
| `requeststoken/observatory_migrate.py` | Optional read-only SQLite import into an empty Redis namespace |
| `static/js/observatory-stats.mjs` | Calendar periods, gap-preserving history and CSV |
| `static/js/observatory.mjs` | Charts, controls, source status and accessible project dialogs |
| `static/observatory/adoption-evidence.json` | Reviewed project manifest and downloadable evidence |
| `static/observatory/download-snapshot.json` | Fixed, dated fallback audit |
| `static/observatory/download-proof-2026-10-25.zip` | Original SQL, raw responses, CSV and integrity hashes |
| Redis `{requeststoken:observatory:v1}:state` | Latest snapshot and daily source-check history; runtime data, not committed |

The existing homepage, `/api/token`, and `/api/requests` keep their current
contracts. The latter uses pypistats with mirrors excluded and reported rolling
windows. Its metrics must not be silently combined with the observatory's
ClickHouse raw/aggregate counts and UTC calendar windows.

## Current source and audited baseline

The public endpoint is `https://sql-clickhouse.clickhouse.com/`, using its
documented public username `demo`. It is not a secret. SQL is generated
server-side for the fixed package `requests`; callers cannot supply SQL.

- Daily history: `SUM(count)` grouped by date from `pypi.pypi_downloads_per_day`.
- Historical/latest raw counts: `COUNT()` from `pypi.pypi`.
- Overflow modes are `throw`, so a public-service limit fails instead of silently
  returning a truncated count. Unexpected row counts and malformed/regressed
  histories also fail validation.

The bundled audit was fetched **2026-10-25 00:51:08 UTC** and ends on
**2026-10-24 UTC**. It contains **34,524,196** raw records for the latest date,
**39,530,831,689** historical raw records from **2016-01-22**, and **3,850**
reported dates with **79** missing dates. The daily aggregate sum is
**39,532,848,966**, exceeding the raw baseline by **2,017,277**. The UI computes
and discloses this difference for each snapshot.

This historical total is not a verified count since Requests' first release.
Downloads are not unique users, completed installs, or identifiable company
activity. Repeated CI jobs and partial transfers can contribute records. Historic
metric definitions changed; the original baseline was not backfilled with the
candidate distribution-filename filter in the BigQuery proof query.

References:

- [PyPI download dataset](https://docs.pypi.org/api/bigquery/)
- [ClickPy ingestion and daily exports](https://clickhouse.com/blog/clickpy-2-trillion-rows)
- [PyPI count-definition changes](https://blog.pypi.org/posts/2026-08-31-download-counts/)

## Refresh and storage behavior

Requests read the shared snapshot without waiting for upstream queries. A short
Redis Lua script reads the snapshot and, when due, obtains a 120-second lease
for one background job. The job performs bounded HTTP source queries, validates
the results, and publishes the snapshot and that UTC day's source-check record
together. A unique token checked during publication prevents an expired worker
from overwriting newer data, releasing another worker's lease or changing its
retry timer. Successful checks wait an hour; failures retain the last snapshot
and wait ten minutes. Timers use Redis expiry rather than application clocks.
No visit means no refresh; there is no separate scheduler or realtime collector.

The browser rechecks every five minutes while visible, on returning to the tab,
or on manual refresh. While a shared job is running, it checks its status every
ten seconds. It reads the shared cache, not a separate upstream query per visitor.

Workers on different hosts coordinate through the same Redis endpoint and
namespace. This client targets a single primary endpoint, including a managed
service's primary endpoint; direct Redis Cluster discovery is not implemented.
The lease prevents duplicate jobs on that primary while it is valid. Expiry or
asynchronous failover can still allow overlapping source reads; this is not an
exactly-once collector. Reads are idempotent and publication requires a live
matching token. The feature does not provision Redis or modify the running site.

`REQUESTS_OBSERVATORY_REFRESH=0` disables this worker's upstream attempts but
still reads Redis. With `REDIS_URL` unset, no shared writes or upstream jobs
start. Missing, invalid or unreachable Redis leaves the last locally observed
snapshot visible with `status: fallback`; a new process uses the bundled audit.
There is no independent SQLite refresh fallback that could bypass the shared
lease. Connections and socket reads each have a one-second timeout and no
automatic retries. Invalid cached JSON is never displayed; a successful source
refresh can replace it. Source/client/proxy errors also preserve the snapshot.

## Redis setup and durability

Use Redis 7.4 or newer and the dependencies in `uv.lock` (`redis-py` 8.1.0).
The application user needs `EVAL`, `HGET`, `HSET`, `GET`, `SET`, `EXISTS`, `DEL`
and `PERSIST` on the configured namespace. The optional migration also needs
`WATCH`, `UNWATCH`, `MULTI` and `EXEC`. Do not grant access to unrelated keys.

For local development, start a private Redis instance, for example:

```sh
docker run -d --name requests-observatory-redis \
  -p 127.0.0.1:6379:6379 \
  -v requests-observatory-redis:/data \
  redis:7.4-alpine redis-server \
  --appendonly yes --appendfsync everysec --maxmemory-policy noeviction
export REDIS_URL=redis://127.0.0.1:6379/0
uv sync --frozen
uv run python app.py
```

For production, use a private endpoint with credentials and `rediss://` when
TLS is provided. Keep TLS verification enabled. Supply `REDIS_URL` as a hosting
secret, never in Git or a public frontend variable. The application does not
print Redis URLs/passwords in errors. A separate Redis database/instance with
adequate memory is preferable to sharing an evicting general-purpose cache.

All workers must share `REQUESTS_OBSERVATORY_REDIS_PREFIX` (default
`requeststoken:observatory:v1`). Use a different prefix for previews. Allowed
characters are ASCII letters, digits, `_`, `.`, `:`, `-` (1–120 characters).
For prefix `requeststoken:observatory:v1`, the keys are:

| Key | Content | Expiry |
| --- | --- | --- |
| `{requeststoken:observatory:v1}:state` | Hash: `snapshot` JSON envelope plus `check:YYYY-MM-DD` records | None |
| `{requeststoken:observatory:v1}:lease` | Unique refresh-owner token | 120 seconds |
| `{requeststoken:observatory:v1}:cooldown` | Next-attempt delay | 3,600 seconds after success; 600 after failure |

The snapshot includes the complete returned daily series. Each `check:` field
keeps that UTC day's latest successful check, including fetch time, coverage and
raw counts; it is not a record of every request or every revision. Day/week/month
counter resets never delete either dataset. Source corrections replace absolute
counts and may decrease totals.

Non-expiring keys alone are **not durability**. Enable AOF (for example,
`appendonly yes`, `appendfsync everysec`) on persistent storage, or equivalent
managed persistence, and use `maxmemory-policy noeviction`. With every-second
fsync, a crash can still lose roughly the last second of writes. Monitor memory,
write errors and persistence health; `noeviction` rejects writes when full.
Keep scheduled backups outside the Redis instance/volume and test restoring a
backup into an isolated instance before relying on it. Managed backups or
consistent RDB snapshots are suitable; follow the provider's supported procedure
for backing up multipart AOF. Do not copy an actively changing AOF directory as
if it were a consistent backup. After restoring, check `/api/observatory` for the
expected `latestDate`, `fetchedAt`, daily history and status before resuming refreshes.

References: [Redis persistence](https://redis.io/docs/latest/operate/oss_and_stack/management/persistence/),
[atomic Lua execution](https://redis.io/docs/latest/develop/programmability/eval-intro/),
[redis-py connections](https://redis.readthedocs.io/en/stable/connections.html).

### Optional import from the earlier SQLite version

If the SQLite version was already run, stop all observatory workers and back up
`var/observatory.sqlite` first. This step is unnecessary for a fresh deployment.
Validate the source, then import into an **empty** Redis namespace:

```sh
uv run python -m requeststoken.observatory_migrate var/observatory.sqlite --dry-run
# Set/export the target REDIS_URL (and optional prefix) before the next command.
uv run python -m requeststoken.observatory_migrate var/observatory.sqlite
```

The importer opens SQLite read-only, validates the snapshot/checks and atomically
writes them to Redis. It refuses to overwrite any destination state, lease or
cooldown. It does not copy leases/timers or delete the SQLite file. Keep the
backup until the migrated snapshot/history and Redis backups have been verified.
Do not change namespaces casually after deployment: another namespace starts
without that runtime history. There is no automatic destructive migration.

## API and time semantics

The JSON response contains `snapshot`, `status`, `checkedAt`, `serverTime`,
`refreshing`, and `message`. Snapshot fields are `package`, `firstDate`,
`latestDate`, `rawTotal`, `rawLatest`, `fetchedAt`, `source`, `queries`, and an
ascending array of `{date, downloads}` daily records.

- `status: current` means a source request succeeded, not that ingestion is realtime.
- `cached` indicates a failed subsequent check; `fallback` indicates the saved audit/cache fallback.
- `fetchedAt` belongs to the retained data. `checkedAt` may describe a later failed attempt.
- `latestDate` is the newest represented calendar day, not an event-time watermark.
- `serverTime` lets the client align UTC calendar boundaries with the server clock.

Daily periods start at 00:00 UTC; weekly periods start Monday; calendar months
start on the first. A newly opened period with no rows displays Pending. An
explicit source-reported zero remains zero. Open periods are partial and missing
historic dates remain unknown. Corrections can decrease totals. Countdown ticks
never add downloads, and the historical total never resets.

Date filters select overlapping calendar periods. A weekly/monthly row always
shows that whole period's available count, even when the chosen filter begins
mid-period. CSV includes coverage and status columns; missing values are blank.
The chart breaks its line at missing periods rather than drawing zeros.

## Project evidence and maintenance

The manifest contains 120 projects: 84 technology/enterprise and 36 crypto/Web3,
across 116 distinct organization/community labels. These labels are not a count
of corporate parents. Each entry has its usage explanation, three-step flow,
scope note, immutable revision, source lines, reviewed timestamp and integrity
hashes. 119 entries have code-backed usage details; Elastic's optional extra is
explicitly dependency-only. Avatars are local copies of public repository-owner
images, with their original source URLs retained in the manifest.

OpenAI SDK v0.28.1 and Anthropic SDK v0.2.9 are separate historical cards. They do
not establish current SDK dependencies or live activity, and do not add to the
120-project/116-label totals. The OpenAI tiktoken directory entry remains a
different project with its own evidence.

When updating a project, inspect the specific source, pin the revision and lines,
refresh hashes and its actual review timestamp, and preserve the true usage
scope. Do not turn examples, tests, vendored copies or optional transports into
claims about a whole company's production infrastructure. Existing third-party
provenance and license notices remain applicable. This directory is non-exhaustive
and does not imply endorsement of a token.

## Deferred realtime work

No BigQuery job or credential is included. Official PyPI logs contain event
timestamps, but ingestion is batched. The upstream Fastly rotation interval is
not an end-to-end freshness guarantee; current latency/completeness has not been
established here.

If adding an intraday source later:

1. Choose an owner-operated query project/identity or another verified source.
2. Use a dry run and an explicit byte/cost limit before any billable query. Record
   fixed query windows, job IDs, observed counts and fetch/event timestamps.
3. Compare bounded queries at different times to measure actual observed lag.
   The newest event time alone does not prove completeness.
4. Requery overlapping buckets and replace their absolute counts, allowing late
   arrivals and corrections. All visitors should read a shared cache.
5. Reconcile filtering/coverage against the historical baseline before combining
   metrics. Show source lag and incomplete periods explicitly.

The realtime feature can be completed independently of this PR. The current
directory and daily-source archive do not require it.
