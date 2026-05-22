# Refactor Inventory

**Status:** [VERIFIED] repository snapshot with [INFERRED] risk notes  
**Captured:** 2026-05-22  
**Purpose:** keep the large refactor grounded in measured code, not vibes.

Numbers in this document are lightweight repository scans. Treat them as
directional until replaced by semantic tooling.

## Workspace

[VERIFIED]

- `C:\academy` is a workspace, not a git repository.
- Product repos are `backend/` and `frontend/`.
- Root entry is `README.md`; target architecture is `ARCHITECTURE.md`.
- Root work outputs belong in `_artifacts/`.

## Backend Layout

[VERIFIED]

```text
backend/
|-- apps/       # Django apps, HTTP, CRUD, workers, core tenant/auth
|-- academy/    # hexagonal domain/application/adapters/framework
|-- libs/       # shared libraries
|-- infra/      # AWS/Cloudflare/IaC support
|-- scripts/    # deploy/admin/helper scripts
|-- docs/       # backend docs SSOT
|-- tests/      # backend tests
```

`backend/academy/` currently contains:

- `domain/`
- `application/`
- `adapters/`
- `framework/`

`backend/apps/worker/` currently contains:

- `ai_worker/`
- `messaging_worker/`
- `video_worker/`

`backend/apps/` current responsibility split:

- `apps/api`: Django settings and root/v1 URL routing.
- `apps/core`: tenant/auth/platform models, middleware, services, and views.
- `apps/domains`: product domains.
- `apps/support`: support modules such as AI/analytics.
- `apps/worker`: worker entrypoints.

`backend/apps/domains/` currently contains 27 domain directories:

```text
ai, assets, attendance, clinic, community, enrollment, exams, fees,
homework, homework_results, inventory, landing_public, lectures, matchup,
messaging, parents, progress, results, schedule, staffs, student_app,
students, submissions, teacher_app, teachers, tools, video
```

High-risk fixed points:

- `AUTH_USER_MODEL = "core.User"` pins `apps/core` as a migration and auth
  boundary.
- Django app labels and migration dependencies are not cosmetic. For example,
  some domain labels differ from directory names and appear in migration graphs.
- URL prefixes are deployed contracts. Examples include `/api/v1/media/` for
  video surfaces and `/api/v1/auth/account-recovery/dispatch/` for account
  recovery.
- API settings and worker settings use different app registries; worker boot must
  be tested after model or app-boundary movement.

## Frontend Layout

[VERIFIED]

```text
frontend/src/
|-- app_admin/
|-- app_dev/
|-- app_promo/
|-- app_student/
|-- app_teacher/
|-- auth/
|-- core/
|-- landing/
|-- shared/
|-- styles/
|-- types/
```

The target architecture must include `app_teacher`; omitting it makes the
frontend boundary plan ambiguous.

## Current Guardrails

[VERIFIED]

Backend:

- `pyproject.toml` enables ruff `F821` only.
- No import-linter configuration was found.
- `drf-yasg` is installed and used by some views, but no committed schema
  generation or drift-check command was found by file scan.

Frontend:

- `package.json` has `dev`, `build`, `typecheck`, `lint`, and Playwright scripts.
- No `openapi-typescript` dependency or script was found.
- No `dependency-cruiser` or `eslint-plugin-boundaries` dependency was found.
- `eslint.config.js` has warn-level guards for raw badge spans, inline style
  object literals, and E2E `waitForTimeout`.
- `pnpm-lock.yaml` was not present in the frontend repo snapshot inspected by
  the audit agent.
- `react` is `18.3.1`, while `@types/react` and `@types/react-dom` are `19.x`;
  this may create type noise during large moves.

Frontend dependency risks:

- `src/shared` is app-agnostic as of 2026-05-22; the snapshot reports no
  `shared -> app_*` imports.
- `app_teacher` and `app_student` still have a small number of `@admin/*`
  imports in domains not yet migrated to shared contracts.
- `src/core/router/AppRouter.tsx` is the practical top-level route SSOT.
- E2E contains audit/local/date-stamped specs mixed with durable gates.
- `frontend/e2e/README.md` had a stale `page.waitForTimeout` count. The new
  durable-suite snapshot script excludes local/audit artifact folders and found
  69 occurrences.

## Directional Scan Counts

[VERIFIED as grep counts, not semantic violations]

| Scan | Count | Meaning |
|---|---:|---|
| Backend serializer-related lines | 1129 | API surface is broad enough that manual FE type sync is unsafe |
| Backend tenant-related query/assignment hits | 3486 | tenant scope is widespread and needs automated guardrails |
| Backend cross-domain imports | 104 | semantic snapshot script, non-internal cross-domain imports |
| Backend cross-domain internal imports | 645 | semantic snapshot script, direct imports into models/services/views/api/serializers |
| Backend domain infra imports | 84 | domain code still reaches infra SDK/helper modules |
| Backend adapter -> application imports | 0 | semantic snapshot script; application port/cancellation contracts allowed and concrete adapter -> use-case imports removed |
| Frontend format/status/type hint hits | 360 | SSOT drift likely exists in UI labels, tones, and formatters |
| Frontend source import files | 1047 | files scanned for import boundary snapshot |
| Frontend source text files | 1391 | app/domain moves need automated boundaries |
| Frontend E2E/script files | 225 | durable gates must be separated from audit specs |
| Frontend durable E2E waitForTimeout calls | 69 | excludes `_local`, `_audit`, artifacts, reports, screenshots |
| Frontend cross-app imports | 9 | remaining role-app imports of admin internals |
| Frontend role-app admin imports | 9 | teacher/student app imports of `@admin/*` internals |
| Frontend shared imports app internals | 0 | `shared/` no longer imports role-app internals |

Snapshot commands:

```powershell
cd C:\academy\backend
python scripts\lint\refactor_boundary_snapshot.py

cd C:\academy\frontend
pnpm refactor:inventory
```

## Structural Bottlenecks

[INFERRED from verified layout]

- `apps/` and `academy/` both encode architectural responsibility, but their
  boundary is not mechanically enforced.
- `apps/domains/` is flat, so cross-domain imports are easy and hard to review.
- Tenant filtering appears in many places, so a missed filter is a systemic risk.
- Frontend app tracks share concepts, but type/status/format SSOT is not enforced.
- The target architecture referenced `roadmap.md` before that document existed.
- `shared/` is now app-agnostic; the remaining frontend boundary work is
  role-app imports of `@admin/*` internals.
- Operational notification counts now use a shared contract instead of teacher
  surfaces importing admin notification internals.
- Community post/reply/attachment contracts now live in shared API contracts.
  Student notices/community and teacher developer feedback no longer import
  admin community internals, and patch notes data is product-wide shared data.
- Video access-mode/rule contracts now live in shared API contracts, and the
  reusable video thumbnail UI lives under `src/shared/media/video`. Student video
  playback/home surfaces no longer import admin video internals.
- Lecture/session attendance API now lives in shared API contracts. Admin
  attendance path remains a compatibility facade, while teacher attendance and
  lecture matrix surfaces use the shared contract directly.
- Storage/inventory API, student list/detail API contracts, and student Excel
  utilities now live in shared contract/product modules. Admin paths remain
  compatibility facades, while teacher/student storage and student surfaces no
  longer import those admin internals.
- React runtime/types mismatch and missing lockfile policy can create unrelated
  noise during refactor validation.

## Migration Constraints

- Django app labels and migrations are high risk. Preserve table names and app
  labels until a dedicated migration plan proves otherwise.
- URL compatibility matters for deployed frontend and external users.
- Worker entrypoints must remain stable through the first phases.
- Generated API types require backend schema generation first; this is a Phase 0
  dependency, not an optional polish task.
- Captured verification status: these counts include the session-enrollment,
  notification, community, video, attendance, and storage/students/inventory
  shared-contract slices plus the AI segmentation contract extraction that moved
  pure DTO/validation imports to `academy.domain.ai`. Re-run the snapshot
  commands before each phase because active refactors can change these counts
  quickly.
