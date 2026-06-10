# Incident 2026-06-10 — Global Maintenance Toggle Locked Tenant Screens

**Status:** Resolved  
**Incident window:** 2026-06-10 16:18:26 KST to 18:48:28 KST  
**Primary symptom:** Tenant custom domains redirected to `/maintenance` and showed "지금은 업데이트 반영중입니다."

## Impact

The following non-platform tenants had `Program.feature_flags["maintenance_mode"]`
set and were redirected to the maintenance page:

- `dnb` — DnB보습학원
- `limglish` — 임근혁 영어
- `sswe` — SSWE
- `tchul` — 박철 과학
- `ymath` — Ymath

`hakwonplus` was exempt by frontend routing and was not locked.

## Root Cause

The developer dashboard exposed a global maintenance switch. When enabled,
`PATCH /api/v1/core/maintenance-mode/` wrote `maintenance_mode=true` to every
non-exempt tenant program. The frontend app then read that flag and redirected
all non-exempt tenant routes through `MaintenanceGate`.

Relevant paths after the fix:

- `backend/apps/core/views/tenant_info.py:55` now blocks `enabled=true` with
  `global_maintenance_disabled`.
- `frontend/src/core/router/AppRouter.tsx:110` is the frontend condition that
  redirects when a tenant program still contains `maintenance_mode=true`.
- `frontend/src/app_dev/domains/dashboard/pages/DashboardPage.tsx:51` and
  `:167` now render the maintenance status without an ON switch.

## Evidence

Production audit log:

- `2026-06-10 16:18:26 KST` — `t1_admin97`, `maintenance.toggle`,
  `Maintenance mode ON`, IP `222.107.38.38`, Chrome user agent.
- `2026-06-10 18:48:28 KST` — incident recovery removed the key from the five
  affected tenant programs.
- `2026-06-10 19:22:51 KST` — production API verification attempted
  `enabled=true`; response was `403` with `code=global_maintenance_disabled`,
  and the maintenance key count stayed `0 -> 0`.

## Resolution

Immediate recovery:

- Removed `maintenance_mode` from all affected production tenant program
  `feature_flags`.
- Verified `limglish.kr`, `tchul.com`, `ymath.co.kr`, `sswe.co.kr`, and
  `dnbacademy.co.kr` on desktop and mobile. All returned `200` and no longer
  routed to `/maintenance`.

Permanent prevention:

- Backend now rejects global maintenance ON requests and records a failed audit
  event.
- Backend keeps OFF as an incident-recovery path that removes only the
  `maintenance_mode` key and preserves other feature flags.
- Developer dashboard no longer exposes the global ON switch.
- Regression tests cover blocked ON and safe OFF behavior.

## Verification

Local:

- `python -m pytest apps/core/tests/test_maintenance_mode_view.py -q --tb=short`
- `python -m ruff check apps/core/views/tenant_info.py apps/core/tests/test_maintenance_mode_view.py`
- `python manage.py check --settings apps.api.config.settings.test`
- `python manage.py makemigrations --check --dry-run --settings apps.api.config.settings.test`
- `python -m pytest tests/test_smoke.py -v --tb=short -x`
- `pnpm typecheck`
- `pnpm lint`
- `pnpm build`

CI/deploy:

- Backend run `27268597540`: success.
- Frontend run `27268598527`: success.
- `pwsh scripts/v1/run-deploy-verification.ps1 -AwsProfile default`: PASS, GO.

Production:

- `https://api.hakwonplus.com/health`: `200`, database connected.
- Production API server-side ON attempt: `403 global_maintenance_disabled`.
- Production maintenance key count: `0 -> 0`.
- Desktop and mobile Playwright probes for all five tenant domains:
  `isMaintenance=false`.
