# Academy signed video CDN

## Current ownership

`academy-cdn-video` serves `cdn.hakwonplus.com/tenants/*` from the private
`academy-video` R2 binding. Cloudflare readback on 2026-10-09 confirmed the Worker,
route and binding. `src/index.js` owns request behavior; `wrangler.toml` owns the
source configuration. The backend issues HMAC URLs through
`apps/domains/video/cdn/cloudflare_signing.py` after its existing access checks.

The Worker checks expiry and the signature over `path|exp|kid|uid` before every
R2 read. It rewrites relative HLS playlist URLs with independently signed child
paths, retaining the same expiry and user. A changed tenant/video path cannot
reuse a signature. Binary ranges retain 206/Content-Range; playlists are fetched
in full before rewriting. Missing/expired signatures return 401, invalid
signatures 403, missing storage objects 404 and missing signing configuration 500.

The signing key is imported once per request and reused for the incoming URL
and every child URL. This removes repeated WebCrypto setup for long playlists
without storing keys across requests or changing issued URLs, existing objects,
cache policy or backend authorization. The next request uses the current secret.

## Cost and performance

The Worker reads R2 directly. Response Cache-Control is not evidence of a
Worker Cache API hit; this implementation has no shared edge object cache.
Adding one requires a separate deletion/overwrite/access-expiry design.

On 2026-10-09, the preceding 30-day analytics contained 371,546 Worker requests
and 3 `exceededResources` events. This is a baseline, not proof that repeated key
imports caused all three events or that the optimization removes all limits.
Use exact-version long-playlist validation and subsequent analytics to evaluate
the change. R2 operation savings below the free allowance do not imply an
immediate invoice reduction. Current prices and limits belong to
[Cloudflare Workers pricing](https://developers.cloudflare.com/workers/platform/pricing/)
and [R2 pricing](https://developers.cloudflare.com/r2/pricing/).

## Verification and release

`node --test infra/cdn_worker/test/worker.test.mjs` executes the actual Worker
module and covers master→variant→segment playback, byte ranges, 2,500 independently
signed child URLs with one key import, cross-tenant tampering, expiration, missing
objects and signing-key rotation. Backend Quality Gate runs this contract.
`test/verify-parity.mjs` retains the older algorithm vectors; it does not replace
the actual-module contract.

Before promotion, verify the exact candidate against synthetic `qa-*` objects
in the isolated development R2 bucket and a temporary Worker with a separate
synthetic signing key and no production route. Check complete playback,
long-playlist output, invalid signatures and cleanup zero. Production promotion
uses the current clean main source, successful backend manifest ancestry and
shared production mutation lock described in
`docs/operations/deployment-modes.md`. Read back the existing Worker version,
bindings, routes and compatibility settings first; preserve the existing secret
binding and the production R2 binding. Never print or export the signing secret.

Record the candidate/source hash and returned Cloudflare version/deployment ID,
then verify the production signed playback path and negative authorization cases.
For regression recovery, redeploy the previously recorded Worker version with
unchanged routes, bindings and secret. Do not delete the production Worker,
enable public R2 access, or rotate the backend secret as a rollback shortcut.
Remove only the task's temporary Worker and exact synthetic object prefix and
read back their absence. Backend image changes follow their full isolated
development→preprod→rolling release sequence independently.

Secret rotation is a separate coordinated change: API SSM, Worker secret and
API runtime must retain consistent signing and playback under the same release
owner. Do not apply standalone SSM edits or copy secrets into a terminal.
