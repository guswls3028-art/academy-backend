# Refactor Validation Matrix

**Status:** [PROPOSED] phase gate  
**Purpose:** every phase must prove behavior from static checks to real flows.

Use the smallest set that proves the touched surface, then broaden when a phase
changes shared contracts, tenant/auth, workers, or frontend routing.

## Always Run

```powershell
git -C C:\academy\backend diff --check
git -C C:\academy\frontend diff --check
```

Also run markdown link checks when docs move.

## Backend Static And Unit

| Surface | Commands |
|---|---|
| Python syntax/static | `cd C:\academy\backend; python -m ruff check apps/ academy/` |
| Refactor boundary snapshot | `cd C:\academy\backend; python scripts\lint\refactor_boundary_snapshot.py` |
| Refactor boundary baseline gate | `cd C:\academy\backend; python scripts\lint\refactor_boundary_snapshot.py --enforce-baseline` |
| Refactor touched-file boundary gate | `cd C:\academy\backend; python scripts\lint\refactor_boundary_snapshot.py --strict-touched` |
| ID/domain safety | `cd C:\academy\backend; python scripts\lint\check_id_domain_safety.py` |
| Django config/imports | `cd C:\academy\backend; python manage.py check --settings apps.api.config.settings.test` |
| Migration drift | `cd C:\academy\backend; python manage.py makemigrations --check --dry-run --settings apps.api.config.settings.test` |
| Worker settings drift | `cd C:\academy\backend; python -m pytest tests\test_worker_settings_drift.py -v --tb=short -x` |
| Worker direct boot | `cd C:\academy\backend; $env:PYTHONPATH='C:\academy\backend'; $env:DJANGO_SETTINGS_MODULE='apps.api.config.settings.worker'; python tests\_worker_boot_check.py` |
| Smoke gate | `cd C:\academy\backend; python -m pytest tests\test_smoke.py -v --tb=short -x` |
| Focused tests | `cd C:\academy\backend; python -m pytest <test path>` |
| Broad backend gate | `cd C:\academy\backend; python -m pytest` |

High-risk backend surfaces:

- tenant resolver and middleware;
- auth/login/account recovery;
- notification/Alimtalk payload generation and worker processing;
- Django model/app-label moves;
- worker entrypoints and settings.

## Frontend Static And Build

| Surface | Commands |
|---|---|
| Type safety | `cd C:\academy\frontend; pnpm typecheck` |
| Lint/boundary | `cd C:\academy\frontend; pnpm lint` |
| Production build | `cd C:\academy\frontend; pnpm build` |
| E2E gate | `cd C:\academy\frontend; pnpm test:e2e:gate` |
| Cross-app baseline | `cd C:\academy\frontend; rg -n "@admin/" src\app_teacher src\app_student src\shared src\auth src\landing src\core` |
| Refactor boundary snapshot | `cd C:\academy\frontend; pnpm refactor:inventory` |
| E2E wait baseline | `cd C:\academy\frontend; rg -n "page\.waitForTimeout\(" e2e` |

Local rendering is required for user-facing UI changes:

```powershell
cd C:\academy\frontend
pnpm dev
```

Then inspect the affected page in a real browser and capture evidence when the
change is visual or workflow-critical.

## Account Recovery / Tenant / Alimtalk

Required when touching login, tenant routing, ID recovery, password recovery, or
notification templates:

- backend focused tests for the API/service path;
- frontend auth/recovery render and E2E path;
- tenant 1 E2E account with `[E2E-{timestamp}]` tag and cleanup;
- pending password reset activation through `/api/v1/token/`, including old-password preservation before activation;
- destructive cases: unknown account, ambiguous name/phone, invalid phone, delivery enqueue failure, delivery failure after an existing pending reset, repeated requests;
- Alimtalk payload content check;
- real send only when explicitly required, with message content verified from
  delivered text or provider logs.

Do not write private phone numbers into docs or tests. Use environment/test
configuration for real-send targets.

## Student Domain Core

Required when touching student identity, signup, import, profile writes,
password reset, student-linked OMR/results/homework/clinic, or role-app student
contracts. Owner SSOT: `../domain/student-core.md`.

- verify `tenant -> active Student -> active Enrollment -> consumer projection`
  for the touched workflow;
- use the canonical student services instead of adding serializer/view-only
  identity logic;
- reject malformed phone numbers instead of silently switching identity mode;
- verify no public password reset changes the actual password before pending
  reset activation;
- verify staff password reset requires authenticated active staff membership and
  rolls back on notification delivery failure;
- for OMR/results/homework/clinic, prove deleted/inactive/cross-tenant
  enrollment is rejected;
- for user-visible changes, verify the producer role and the student/parent
  consumer role.

Focused backend set:

```powershell
cd C:\academy\backend
python -m pytest apps\domains\students\tests\test_student_identity_convergence.py apps\domains\students\tests\test_registration_password_safety.py apps\domains\students\tests\test_password_reset_safety.py apps\domains\students\tests\test_account_recovery.py -v --tb=short -x
```

Add when student-linked content is touched:

```powershell
cd C:\academy\backend
python -m pytest apps\domains\students\tests\test_student_domain_stabilization.py apps\domains\results\tests\test_submission_scope_guard.py apps\support\omr\tests\test_candidate_matching.py -v --tb=short -x
```

Launch readiness uses the stricter gate in
`student-domain-launch-readiness.md`.

## Worker Validation

Required when touching messaging, AI, video, queue, or worker settings:

- worker import/boot check (`PYTHONPATH` must include `C:\academy\backend`
  when running `tests\_worker_boot_check.py` directly);
- focused worker unit test;
- queue message shape compatibility check;
- production deploy verification if worker code is deployed.

## Deployment Validation

Backend:

- inspect executable workflow under `.github/workflows/`;
- verify migration requirement;
- verify API and affected worker deployment path;
- run smoke checks after deploy.
- optional manual deploy verifier:
  `cd C:\academy\backend; pwsh scripts/v1/run-deploy-verification.ps1 -AwsProfile default`.

Frontend:

- build locally;
- verify Cloudflare Pages deployment or preview URL when deployed;
- run focused Playwright against the deployed target when the change affects
  public/user flows.

Known executable deploy truth:

- Backend CI/CD: `backend/.github/workflows/v1-build-and-push-latest.yml`.
- Frontend CI/CD: `frontend/.github/workflows/quality-gate.yml`.
- API container: `backend/docker/api/Dockerfile`.
- Messaging worker container: `backend/docker/messaging-worker/Dockerfile`.
- AI worker container: `backend/docker/ai-worker-cpu/Dockerfile`.
- Video Batch container: `backend/docker/video-worker/Dockerfile`.

## Reporting Format

Every phase report must classify each check:

- `passed`
- `failed`
- `skipped-not-touched`
- `skipped-blocked`
- `needs-manual-validation`

Skipped checks require a reason. Manual checks require the exact evidence still
needed.
