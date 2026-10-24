# Deploy (mercury / Dokploy)

Dokploy project `requeststoken.org` → compose `requeststoken-org-web` (composeId `Ez8N39E_NDdLs_VuH3uKI`, sourceType raw).
Domain `requeststoken.org` → service `web`, port 5042, Let's Encrypt.

The image is built on the server from a git commit (no registry):

    git archive --format=tar <commit> | ssh root@mercury.kennethreitz.org \
      'rm -rf /tmp/rtbuild && mkdir /tmp/rtbuild && tar -x -C /tmp/rtbuild && docker build -t requeststoken-org:<commit> /tmp/rtbuild'

Then update the image tag in the compose file (Dokploy UI or `compose.update`) and redeploy (`compose.deploy`).

## Analytics

The tracker is Umami at `https://analytics.kennethreitz.org/script.js`. It is not in the page until `UMAMI_WEBSITE_ID` is set.

1. In that Umami dashboard, add a website for `requeststoken.org` and copy its website ID.
2. Set `UMAMI_WEBSITE_ID` on the Dokploy service environment for `web`.
3. Redeploy.

`UMAMI_SCRIPT_URL` is optional. Leave it unset to keep the host above.
