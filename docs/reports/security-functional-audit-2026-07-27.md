# Security and Functional Audit — 2026-07-27

## Status

- Scope: advertised Matchup/PPT/OMR/attendance/staff/video/Problem Studio flows, authentication boundaries, tenant isolation, uploads, browser delivery headers, production dependencies, and external AI processing.
- Code snapshots at audit start: backend `2bed96d5b`, frontend `36241465`.
- Remediation status: implemented and locally verified.
- Production status: deployment and post-deploy verification pending. This workspace was not released during the audit because backend `main` had diverged from `origin/main` and both repositories contained unrelated concurrent work; publishing from that state could include changes outside this audit.
- This report records engineering and operational evidence. It is not a substitute for Korean privacy-law advice.

## Confirmed Findings and Resolution

| ID | Severity | Finding | Resolution | Evidence |
|----|----------|---------|------------|----------|
| SAF-01 | High functional | OpenCV 5 changed `cv2.convexityDefects` output from `(N, 1, 4)` to `(N, 4)`. OMR marker classification raised `IndexError`, the broad fallback returned `method="raw"`, and rotated/noisy sheets lost marker homography. | Normalize both layouts with `np.asarray(defects).reshape(-1, 4)`. Pin `opencv-python-headless==5.0.0.93` in the shared constraints file. Add a two-layout regression test. | OpenCV 5.0.0; OMR compatibility and real-use flow: 33 passed. |
| SAF-02 | High browser hardening | Cloudflare Pages Function responses omitted CSP, anti-framing, nosniff, referrer, permissions, and complete HSTS headers. `_headers` does not cover responses created by Pages Functions. | Wrap every Function response with canonical headers. HTML receives a per-response 128-bit nonce; all script tags receive that nonce; inline script attributes are blocked. Static `_headers` carries the fallback policy and anti-framing headers. | Local Pages runtime returned CSP, `frame-ancestors 'none'`, `X-Frame-Options: DENY`, nosniff, referrer, permissions, and preload HSTS. Six inline scripts received matching nonces and no CSP console error occurred. |
| SAF-03 | High privacy/compliance | Production used `global.amazon.nova-2-lite-v1:0`, but the privacy policy described AWS processing as Seoul-only and the CTA only said “external AI provider.” | Record the exact Amazon Bedrock provider, global routing countries, transferred image content, transfer timing/method, deletion/retention boundary, and opt-out path in privacy policy v1.3. Require explicit UI confirmation and masking guidance before transcription or Beta rewrite. Keep tenant-prefixed temporary storage and terminal deletion. | AWS account exposes only the active global Nova 2 Lite profile from Seoul; the UI and public privacy page render the corrected disclosure. |
| SAF-04 | High dependency | Axios 1.16.0, DOMPurify 3.4.11, linkify-it 5.0.1, and React Router 6.30.4 had published advisories. | Upgrade to Axios 1.18.1, DOMPurify 3.4.12, linkify-it 5.0.2, and React Router 7.18.1. Apply compatible `brace-expansion` 1.1.16, 2.1.2, and 5.0.8 overrides. | Typecheck, lint, guards, and production build pass. Direct Axios/DOMPurify/linkify/open-redirect advisories are cleared. |

## Controls Confirmed

- Tenant-scoped selectors and storage keys reject cross-tenant access; no cross-tenant fallback was found in the tested Matchup, OMR, attendance, staff, video, or presign paths.
- Login uses distributed IP and account throttles, refresh rotation, and blacklist controls. The four-character minimum is an explicit product rule and was not silently changed.
- API CORS did not reflect an unapproved origin, while the approved production origin was allowed.
- Upload, ZIP member/count, XML, PDF, and source-package limits are bounded. The local Expat entity-amplification limit rejected the larger expansion sample.
- HTML rendering paths reviewed in this audit sanitize untrusted rich content with DOMPurify.
- SSRF-sensitive URL fetching validates HTTPS and rejects private, loopback, and link-local destinations.
- Bandit high-severity hash findings were non-security MD5/SHA-1 uses for ETags, filenames, fingerprints, cache keys, or deduplication identifiers; no password or signature use was found.
- `pip-audit` identified an old setuptools only in the developer virtual environment, not in declared production runtime requirements.

## Dependency Reachability Decisions

`pnpm audit --prod` now reports two advisory classes (three high instances):

1. `react-router@7.18.1` is flagged for an unstable RSC action/CSRF path. This SPA uses declarative `BrowserRouter`, React 18, no RSC APIs, no framework/data router SSR, and no server action execution. React Router 8.3 requires React 19.2.7 and Node 22.22, so a forced major runtime migration is not proportionate to this unreachable path.
2. `brace-expansion` remains flagged for unbounded output length in the Node-side `exceljs -> archiver/unzipper -> glob` dependency graph. The production Vite `vendor-excel` bundle contains none of `brace-expansion`, `minimatch`, `archiver`, `readdir-glob`, `glob`, `fstream`, or `rimraf`; no attacker-controlled server glob path exists in the browser application. Compatible patch releases were still applied for the separate exponential-expansion advisory.

These are documented reachability exceptions, not muted audit entries.

## Residual Architectural Risks

- JWT access and refresh tokens remain in `localStorage`. A nonce-based CSP, blocked inline attributes, DOMPurify upgrades, anti-framing, and strict script origins materially reduce XSS exposure, but do not provide the theft resistance of an HttpOnly refresh cookie. Moving refresh tokens to `Secure; HttpOnly; SameSite` cookies requires a coordinated backend refresh/CSRF/session migration and is tracked as a separate auth architecture change.
- Nova 2 Lite processing is global. AWS documents encrypted cross-region routing, default zero operator access, default zero data retention for Nova input/output, and no training on customer prompts, but global routing is still unsuitable where data must remain in Korea. The user must mask unnecessary personal data or not use the feature.
- Privacy policy v1.3 is an urgent corrective disclosure of existing processing. The operator should have Korean privacy counsel review the final legal basis, notice timing, and processor contract wording.
- Password length remains four characters by explicit workspace policy. Distributed throttling, password hashing, access-token lifetime, refresh rotation, and blacklist controls reduce but do not eliminate credential-guessing risk.

## Verification Record

- Backend:
  - OpenCV 5.0.0.93 confirmed.
  - `tests/test_omr_marker_detector_compat.py` plus OMR tenant real-use suite: 33 passed.
  - Django system check and migration drift check: pass.
  - Problem Studio transcriber and infrastructure safety contract: 45 passed.
  - Focused Ruff and touched-file whitespace checks: pass.
  - Earlier full audit groups: 277 passed, 5 skipped across authentication/tenant isolation, Matchup/PPT/OMR, attendance/staff/video.
- Frontend:
  - `pnpm typecheck`: pass.
  - `pnpm lint`: pass with one unrelated warning in untracked `src/wrong-note-preview.tsx`.
  - `pnpm guard:legacy-api`: pass, including E2E safety and strict-import guards.
  - `pnpm build`: pass; 7,451 modules transformed and 482 JavaScript bundle budgets checked.
  - Problem Studio AI typing desktop/mobile mock-worker regression: 1 passed.
  - Local Cloudflare Pages Function `/privacy`: HTTP 200, corrected v1.3 disclosure visible, no browser console warning/error.

## Authoritative External References

- AWS Nova 2 Lite regional availability and global destinations: <https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-amazon-nova-2-lite.html>
- AWS cross-region inference encryption and destination logging: <https://docs.aws.amazon.com/bedrock/latest/userguide/cross-region-inference.html>
- AWS Bedrock abuse detection and zero-data-retention defaults: <https://docs.aws.amazon.com/bedrock/latest/userguide/abuse-detection.html>
- Cloudflare Pages response-header behavior: <https://developers.cloudflare.com/pages/configuration/headers/>
