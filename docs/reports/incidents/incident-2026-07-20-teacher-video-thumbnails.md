# Incident 2026-07-20 — Teacher Video Thumbnails Not Rendered

**Status:** Resolved

**Incident date:** 2026-07-20 KST

**Primary symptom:** Teacher video cards on `tchul.com` showed only the play-icon fallback although uploaded-video thumbnails existed.

## Impact

The teacher session video tab and teacher-wide video list did not render uploaded
video thumbnails. The media API, transcode records, R2 objects, and CDN delivery
remained healthy; this incident did not require a backend data repair or worker
redeploy.

## Root Cause

The media API already returned `thumbnail_url`, but the two teacher surfaces did
not consistently consume it:

- `frontend/src/app_teacher/domains/lectures/pages/SessionDetailPage.tsx`
  rendered a play icon unconditionally.
- `frontend/src/app_teacher/domains/videos/pages/VideoListPage.tsx` rendered a
  derived YouTube thumbnail but ignored uploaded-video `thumbnail_url`.

The defect was therefore in frontend projection, not thumbnail generation or
storage.

## Resolution

- Added the teacher-local
  `frontend/src/app_teacher/domains/videos/components/CompactVideoThumbnail.tsx`
  component and used it from both affected teacher screens.
- The component renders the API image, resets its error state when the URL
  changes, and falls back to the play icon for a missing URL or failed image.
- The thumbnail remains decorative so the adjacent video title is not announced
  twice by assistive technology.
- Added
  `frontend/e2e/teacher/video-thumbnail-render.mock.spec.ts` for the real teacher
  navigation path, mobile and desktop layouts, decoded image pixels, null input,
  and HTTP 404 fallback.

`CompactVideoThumbnail` is shared by the two compact teacher lists. It is
separate from the application-wide
`frontend/src/shared/media/video/VideoThumbnail.tsx`, whose API and layout serve
other video surfaces.

## Verification Evidence

- Production API query for session `245` returned two `READY` videos with
  tenant-scoped `thumbnail_url` values.
- R2 object probes returned non-zero JPEG objects for videos `448` and `449`;
  sanitized CDN probes returned HTTP 200 with `image/jpeg`.
- Both transcode-job records were `SUCCEEDED`, one attempt, with no recorded
  error. Historical per-job logs had expired under the 30-day retention policy;
  a current worker publication log showed the expected upload/publish completion
  path.
- Frontend commit `96c71d30bbc72b5792e3a702deace1bc0f4e8fc7` deployed through
  Frontend Quality Gate run `29748923162`; all quality, Cloudflare deployment,
  production login, tenant availability, and roundtrip jobs succeeded.
- `tchul.com/version.json` matched the deployed commit.
- On the reported session, both thumbnails decoded at `1280x720` in 390px mobile
  and 1366px desktop viewports. The teacher-wide list loaded all 20 first-page
  thumbnail slots after lazy-loading was exercised.
- Production verification was read-only and created no tenant data.

## Prevention

- Both compact teacher video lists use one rendering owner.
- The focused E2E locks normal, missing, failed-load, mobile, and desktop states.
- Thumbnail incident triage now distinguishes DB/worker, API/signing,
  R2/CDN delivery, and final browser rendering. See
  [video-batch.md](../../operations/runbooks/video-batch.md#51-thumbnail-not-visible-triage).
- The production canary's `READY videos missing thumbnails` warning is treated
  as a DB invariant only; browser rendering still requires API/CDN/DOM proof.

## Release Reference

- [v1.9.2 release notes](../../releases/v1.9.2.md)
- [release index](../../releases/README.md)
