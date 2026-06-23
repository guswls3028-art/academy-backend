# Refactor Inventory

**Status:** [VERIFIED] repository snapshot with [INFERRED] risk notes  
**Captured:** 2026-06-23
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
- `frontend/e2e/README.md` had a stale `page.waitForTimeout` count. The current
  refactor snapshot script excludes local/audit artifact folders and found
  34 occurrences.

## Directional Scan Counts

[VERIFIED as grep counts, not semantic violations]

| Scan | Count | Meaning |
|---|---:|---|
| Backend serializer-related lines | 1129 | API surface is broad enough that manual FE type sync is unsafe |
| Backend tenant-related query/assignment hits | 3486 | tenant scope is widespread and needs automated guardrails |
| Backend cross-domain imports | 117 | semantic snapshot script, non-internal cross-domain imports; increased by deliberate selector/service boundary use |
| Backend cross-domain internal imports | 604 | semantic snapshot script, direct imports into models/services/views/api/serializers |
| Backend domain infra imports | 82 | domain code still reaches infra SDK/helper modules |
| Backend adapter -> application imports | 0 | semantic snapshot script; application port/cancellation contracts allowed and concrete adapter -> use-case imports removed |
| Frontend format/status/type hint hits | 360 | SSOT drift likely exists in UI labels, tones, and formatters |
| Frontend source import files | 1091 | files scanned for import boundary snapshot |
| Frontend source text files | 1455 | app/domain moves need automated boundaries |
| Frontend E2E/script files | 225 | durable gates must be separated from audit specs |
| Frontend durable E2E waitForTimeout calls | 34 | excludes `_local`, `_audit`, artifacts, reports, screenshots |
| Frontend same-app domain imports | 146 | role app domain internals still import sibling domain internals |
| Frontend large files | 34 | files large enough to make safe UI/domain movement harder |
| Frontend local format definitions | 121 | repeated formatting helpers/status-adjacent logic still need SSOT cleanup |
| Frontend status map definitions | 35 | repeated status/tone maps still need SSOT cleanup |
| Frontend query key literals | 901 | query key factory adoption remains incomplete |
| Frontend inline style objects | 3806 | existing style guard baseline remains broad |
| Frontend raw badge classes | 21 | existing badge guard baseline remains non-zero |
| Frontend API response type definitions | 102 | generated API type path is still absent |

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
- `shared/` is app-agnostic and role-app cross-imports are at 0. The next
  frontend boundary work is enforcing this baseline mechanically and reducing
  large-file/wait-pattern debt without reintroducing app internals.
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
- Fees API contracts now live in `frontend/src/shared/api/contracts/fees.ts`,
  and fees status/tone labels live in `frontend/src/shared/product/fees/feesStatus.ts`.
  Admin fees paths remain compatibility facades, while teacher fees surfaces no
  longer import admin fees internals. Teacher fees now renders an explicit
  permission state for 403/disabled-feature responses instead of presenting a
  misleading empty billing surface.
- Local fees E2E enables the feature only through browser route interception in
  `frontend/e2e/helpers/feesFeatureFlag.ts`. This is not a database or
  production flag mutation; production QA must respect the actual
  `fee_management` tenant flag.
- Tools timer download API now lives in `frontend/src/shared/api/contracts/tools.ts`.
  The admin stopwatch timer API path remains a compatibility facade, while the
  teacher timer surface no longer imports admin tools internals.
- Exam enrollment API now lives in
  `frontend/src/shared/api/contracts/examEnrollments.ts`. The admin exam
  enrollment API path remains a compatibility facade, while teacher exam detail
  and OMR pages use the shared `enrollment_id` contract directly.
- Tenant information API now lives in
  `frontend/src/shared/api/contracts/tenantInfo.ts`. The admin profile API path
  re-exports the tenant info contract, while the teacher organization settings
  page imports shared directly.
- Submission types and submission inbox/action APIs now live in
  `frontend/src/shared/api/contracts/submissions.ts`. Admin submissions type and
  materials submission API paths plus the teacher submissions API path remain
  compatibility facades, while the student submit page and teacher submissions
  inbox import shared directly.
- Lecture section and section-assignment API contracts now live in
  `frontend/src/shared/api/contracts/lectureSections.ts`. The admin lectures
  sections API path remains a compatibility facade, while teacher clinic imports
  shared directly.
- Clinic participant/session/idcard student and enrollment reads now use
  `students.selectors` / `enrollment.selectors` instead of direct
  `Student.objects` / `Enrollment.objects` lookups in the touched clinic HTTP
  paths. Soft-deleted students are no longer accepted as clinic participant
  targets through `student`, `enrollment_id`, or student self-booking, and
  deleted student accounts receive an empty clinic idcard response.
- Clinic participant status, complete, and uncomplete writes now route through
  `apps.domains.clinic.services.lifecycle`. The service owns transition maps,
  completion guards, row locking, and notification event selection.
- Clinic participant creation and booking-change writes now route through
  `apps.domains.clinic.services.lifecycle` as well. The service owns
  tenant/student/enrollment validation, capacity and duplicate checks, status
  defaults, cancel-after-new-booking semantics, and notification event context.
- Attendance roster create now validates posted student IDs through
  `students.selectors`, scopes `AttendanceSerializer` session/enrollment FK
  querysets to the request tenant, and delegates writes to
  `attendance.services.create_attendance_roster`. Session-enrollment bulk create
  shares `ensure_session_roster_membership`, so enrollment reactivation, fee
  auto-assignment, session roster creation, and attendance idempotency cannot
  drift between the two public APIs.
- Messaging manual send and manual notification preview now resolve active
  tenant student/parent recipients through
  `messaging.services.recipients.resolve_student_message_recipients` instead
  of direct `Student.objects` reads in messaging HTTP/preview paths.
- Results admin student grades now validates `student_id` safely and resolves
  the target through `students.selectors.active_student_by_id`, rejecting
  malformed, cross-tenant, and same-tenant soft-deleted students before grade
  enrollment reads.
- NotificationLog `source_tenant_id`, OMR detected-answer `exam_question_id`,
  and OMR student-match `enrollment_id` now use Django ForeignKey fields while
  preserving the deployed DB column names. The ID-domain safety guard reports
  zero new integer-FK errors in the 2026-06-23 snapshot. A follow-up guardrail
  cleanup removed `SILENT_FALLBACK` warnings and reduced deterministic row
  selection debt to the strict-safe subset; the remaining 36 warnings are 28
  `[ALLOWED]` integer-FK candidates plus 8 `UNORDERED_FIRST` instances in
  files that need boundary extraction before touched-file strict cleanup.
- Frontend display string helpers for common date, money, and byte labels now
  live in `frontend/src/shared/utils/displayText.ts`. Dev tenant and assessment
  query-key literals were folded into shared key factories, assessment homework
  list/policy contracts moved behind `frontend/src/shared/api/contracts/assessments.ts`,
  and the refactor budget gate is back under baseline.
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
  pure DTO/validation imports to `academy.domain.ai`, the fees shared
  contract/status slice, the tools timer download contract slice, the exam
  enrollment contract slice, the tenant info contract slice, the submissions
  contract slice, the lecture sections contract slice, the clinic
  active-student selector boundary slice, the clinic participant transition
  service slice, the attendance roster service boundary slice, the messaging
  recipient resolver slice, the results student-grades selector slice, and the
  assessment homework shared-contract slice. Re-run the snapshot commands before each phase
  because active
  refactors can change these counts quickly. The latest backend snapshot
  (2026-06-23) reports `cross_domain_import=117`,
  `cross_domain_internal_import=604`, and `domain_infra_import=82`.
