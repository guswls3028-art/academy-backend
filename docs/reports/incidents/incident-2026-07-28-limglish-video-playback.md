# Incident 2026-07-28 — Limglish Student Video Playback

**Status:** Resolved; recurrence prevention pending production rollout

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
- Changed API and worker defaults to the canonical protected CDN.
- Made production settings reject a non-canonical CDN URL or signing secret
  shorter than 32 characters.
- Made the API environment inspection command require and mask the signing
  secret.
- Added the same fail-closed validation to `Sync-ApiEnvFromSSOT` before any
  SSM write or API refresh.
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
- Read-only production browser verification followed the student dashboard,
  video menu, course `422`, session `394`, and video `562`.
  Desktop `1366x768` and mobile `390x844` both rendered and played the video
  without page errors, console errors, or player error text.
- Local verification passed Django checks, migration drift checks, Ruff,
  submission and refactor boundary guards, PowerShell parsing, and 27 smoke
  tests plus five subtests.

## Prevention

- Production startup now fails before serving traffic when the video delivery
  configuration is unsafe.
- Deployment synchronization now fails before mutating SSM or replacing an API
  instance when the same contract is unsafe.
- The canonical CDN contract is documented in
  [ssm-json-schema.md](../../operations/ssm-json-schema.md).
- Production API replacement must preserve at least one healthy target through
  the drain-and-refresh sequence.

## Release Reference

The sealed release and deployment evidence will be linked after the recurrence
prevention rollout completes.
