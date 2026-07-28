# Incident 2026-07-28 — Limglish Student Video Playback

**Status:** Resolved; recurrence prevention deployed and production-verified

**Incident date:** 2026-07-28 KST

**Primary symptom:** The Limglish site was briefly unavailable, and after API
recovery student video pages loaded without playable video.

## Impact

- The tenant reported site unavailability at 15:06 KST while students were
  watching videos.
- The API was reported recovered at 15:17 KST, but video playback was still
  unavailable at 15:20 KST.
- Video records, transcoded HLS objects, and thumbnails were not lost or
  corrupted.

## Root Cause

The production `/academy/api/env` parameter did not contain
`CDN_HLS_BASE_URL`, `CDN_HLS_SIGNING_SECRET`, or
`CDN_HLS_SIGNING_KEY_ID`.

That omission crossed two unsafe fallback paths:

- `apps/api/config/settings/base.py` defaulted `CDN_HLS_BASE_URL` to the
  bucket's private `r2.dev` development URL.
- `apps/domains/video/views/playback_mixin.py` returned an unsigned playback
  URL when `CDN_HLS_SIGNING_SECRET` was empty.

The playback API therefore returned HTTP 200 while issuing an unusable unsigned
R2 URL. The URL itself returned HTTP 401 because public bucket browsing is
disabled. The protected Cloudflare CDN remained healthy and correctly rejected
unsigned requests.

The v1 API environment synchronization path did not require the CDN URL or
signing secret, so the broken configuration was allowed to reach a running
instance instead of failing the deployment.

During the first configuration refresh, the API Auto Scaling Group had only one
desired instance. Replacing that instance created a brief overlap with no
healthy target and caused the secondary site outage. The service was restored
with two healthy targets before the old instance was drained and refreshed,
then returned to its normal desired capacity.

## Resolution

- Restored the production API SSM values:
  - `CDN_HLS_BASE_URL=https://cdn.hakwonplus.com`
  - the active CDN signing secret
  - `CDN_HLS_SIGNING_KEY_ID=v1`
- Refreshed API instances with a two-instance drain-and-replace sequence and
  returned the ASG to desired capacity 1.
- Changed the API default to the canonical protected CDN.
- Made production settings reject a non-canonical CDN URL or signing secret
  shorter than 32 characters.
- Made the API environment inspection command require and mask the signing
  secret and require the active signing key ID. The signing secret remains
  masked even with verbose output.
- Added the same fail-closed validation to `Sync-ApiEnvFromSSOT` before any
  SSM write or API refresh.
- Extended the isolated API pre-production canary to fetch a real signed HLS
  master playlist, signed variant playlist, and ranged media segment before
  any production env or ASG mutation.
- Added settings and static runtime-contract regression tests.

## Verification Evidence

- Production video `562` is `READY`; its transcode job is `SUCCEEDED`.
- R2 directly reported a non-empty master playlist, thumbnail, and 738 HLS
  objects totaling 1,087,948,633 bytes.
- The historical Batch job log had expired under the 30-day CloudWatch
  retention policy; the durable job state and objects confirm successful
  publication.
- A fresh student playback request for enrollment `1304` returned:
  - playback API: HTTP 200
  - signed CDN master playlist: HTTP 200
  - signed variant playlist: HTTP 200
  - ranged media segment: HTTP 206
- After the recurrence-prevention deployment, the same chain was executed from
  the live API container with a freshly issued JWT for the affected student:
  `api=200 master=200 variant=200 segment=206 host=cdn.hakwonplus.com kid=v1`.
- The deployment candidate passed an isolated pre-production R2 -> signed CDN
  master -> variant -> ranged segment canary before any production runtime
  update.
- Pre-deployment read-only browser verification followed the student dashboard,
  video menu, course `422`, session `394`, and video `562`. Desktop `1366x768`
  and mobile `390x844` fetched the playback API, master playlist, variant
  playlist, and media segments without page, console, or player errors. Those
  captures did not independently prove playback-clock advancement and are not
  used as the post-deployment proof.
- Local verification passed Django checks, migration drift checks, Ruff,
  submission and refactor boundary guards, PowerShell parsing and deployment
  contracts, 37 smoke/CDN tests plus five subtests, 42 video
  callback/progress tests plus nine subtests, and 37 security/access tests plus
  16 subtests.
- Backend workflow
  [30341862808](https://github.com/guswls3028-art/academy-backend/actions/runs/30341862808)
  completed successfully through isolated pre-production proof, migrations,
  safe API refresh, runtime digest verification, manifest promotion, and lock
  release. Attempts
  [30339694570](https://github.com/guswls3028-art/academy-backend/actions/runs/30339694570)
  and
  [30341008029](https://github.com/guswls3028-art/academy-backend/actions/runs/30341008029)
  failed closed before production mutation because of shell and line-ending
  portability defects; both defects were corrected before the successful run.
- Frontend workflow
  [30338703381](https://github.com/guswls3028-art/academy-frontend/actions/runs/30338703381)
  completed typecheck, lint, build, deployment, production canary, and tenant
  availability checks.
- The post-deploy production canary returned 30 PASS / 0 WARN / 0 FAIL. The
  read-only deployment verifier returned CONDITIONAL GO solely because the
  local operator shell could not run `wrangler r2 bucket list`; the direct R2
  object inventory and authenticated signed-CDN chain above provide the
  video-specific evidence.

## Prevention

- Production startup now fails before serving traffic when the video delivery
  configuration is unsafe.
- Deployment synchronization now fails before mutating SSM or replacing an API
  instance when the same contract is unsafe.
- Every candidate API image and environment must complete the read-only signed
  CDN master -> variant -> segment chain in the isolated pre-production canary
  before production promotion.
- The canonical CDN contract is documented in
  [ssm-json-schema.md](../../operations/ssm-json-schema.md).
- Production API replacement must preserve at least one healthy target through
  the drain-and-refresh sequence.
- Student playback retries now refetch the playback API to obtain a new signed
  URL, and CDN authorization or service failures are reported as service-side
  failures instead of blaming the student's device or network.

## Release Reference

- Backend runtime release:
  `sha-e4549649c56874cc85eb7becb1777752aef47211-run-30341862808-1`
- Promoted manifest commit: `e4e4c5e84`
- Frontend production commit: `f72ad5e4`
- Sealed release: [v1.11.36](../../releases/v1.11.36.md)
