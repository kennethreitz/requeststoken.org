# Deploy (mercury / Dokploy)

Dokploy project `requeststoken.org` → compose `requeststoken-org-web` (composeId `Ez8N39E_NDdLs_VuH3uKI`, sourceType raw).
Domain `requeststoken.org` → service `web`, port 5042, Let's Encrypt.

The image is built on the server from a git commit (no registry):

    git archive --format=tar <commit> | ssh root@mercury.kennethreitz.org \
      'rm -rf /tmp/rtbuild && mkdir /tmp/rtbuild && tar -x -C /tmp/rtbuild && docker build -t requeststoken-org:<commit> /tmp/rtbuild'

Then update the image tag in the compose file (Dokploy UI or `compose.update`) and redeploy (`compose.deploy`).

## Observatory Redis

The observatory revision requires a Redis service for automatic shared updates.
Provision it separately, with persistent storage/AOF, backups and `noeviction`,
then add the private connection string as a Dokploy environment secret. Pass it
through to the **newly built** web image, for example in the existing service:

```yaml
services:
  web:
    environment:
      PORT: "5042"
      REDIS_URL: ${REDIS_URL}
      REQUESTS_OBSERVATORY_REDIS_PREFIX: requeststoken:observatory:v1
```

Use a private network endpoint reachable from the web container; its own
`127.0.0.1` is not a separate Redis container. Prefer authenticated `rediss://`
for remote Redis and retain TLS verification. All production web workers must
share the endpoint and namespace; use a separate namespace for preview builds.

After deployment, visit `/observatory` once to trigger a refresh and inspect
`/api/observatory`: `refreshing` should finish, `checkedAt` should advance, and
`status` should become `current` after a successful source check. The upstream
date may remain delayed. Confirm the dated history and source-check records
survive a controlled Redis restart/restore before relying on the setup.

Without `REDIS_URL`, the page still serves the labelled saved audit, with no
upstream jobs. During outages a process serves its last observed snapshot.
`REQUESTS_OBSERVATORY_REFRESH=0` pauses refresh on that worker while retaining
reads; set it on all workers to pause the entire site. Redis does not add a
realtime download source or a scheduler.

The checked-in production compose/image tag is intentionally unchanged; this PR
does not connect to the server or deploy anything. Full persistence, permissions,
backup and optional SQLite-import instructions are in
[the observatory handoff](../docs/observatory.md#redis-setup-and-durability).
