# Phase 1 Execution Plan

**Status:** [PARTIAL / HISTORICAL PLAN] first implementation phase
**Captured:** 2026-05-22  
**Target:** students canonical read/write path  
**Non-goal:** do not merge with current account-recovery/password/Alimtalk release work.

## Working Assumption

The first safe refactor is not a package move. It is a strangler layer inside
the existing students domain that gives every student read/write a canonical
selector or service while keeping deployed URLs stable.

## First Refactor Target

Canonicalize student identity/profile/lifecycle:

- read: list/detail/profile/current student/parent children;
- write: create, update profile, parent relink, soft delete, restore;
- compatibility: existing `/students/*`, `/student/me/`, and registration/import
  endpoints stay live;
- destructive permanent delete has a guarded service shell; domain hook/event
  extraction remains pending.

## Expected Change Files

Backend additions:

- `apps/domains/students/selectors.py`
- `apps/domains/students/services.py` or small service modules such as
  `services/profile.py`, `services/lifecycle.py`, `services/creation.py`
- `apps/domains/students/events.py`
- `apps/domains/students/tests/test_student_contracts.py`
- `apps/domains/students/tests/test_student_tenant_isolation.py`
- `apps/domains/students/tests/test_student_profile_canonicalization.py`

Backend modifications, incremental:

- `apps/domains/students/views/student_views.py`
- `apps/domains/students/views/registration_views.py`
- `apps/domains/students/views/enrollment_matrix_view.py` only after enrollment
  service boundary is ready
- `apps/domains/student_app/profile/views.py`
- `apps/domains/student_app/sessions/views.py`
- `apps/domains/students/services/lecture_enroll.py`
- `apps/domains/students/services/bulk_from_excel.py`
- `academy/adapters/db/django/repositories_students.py`

Frontend modifications after backend contracts are snapshotted:

- `src/app_admin/domains/students/api/students.api.ts`
- `src/app_teacher/domains/students/api.ts`
- `src/app_student/domains/profile/api/profile.api.ts`
- `src/auth/pages/SignupModal.tsx`
- `src/auth/api/recovery.api.ts` only for contract references, not behavior
  change during current release
- new shared/generated student contract path once OpenAPI generation is ready

E2E/helper modifications:

- Replace `/api/v1/students/students/` helper calls with canonical route or
  mark them as expected legacy failures before route deletion.
- Add one durable student propagation E2E using `[E2E-{timestamp}]` tag and
  cleanup.

## Risk Files

| File | Risk |
|---|---|
| `student_views.py` | Large view with create/update/delete/import/permanent-delete logic and cross-domain side effects |
| `registration_views.py` | Public signup approval creates users/students/parents and sends messages |
| `student_app/profile/views.py` | Profile PATCH now routes through `update_student_profile`; response DTO still differs from admin/auth surfaces |
| `lecture_enroll.py` | Create/restore path used by enrollment/import; can affect roster state |
| `bulk_from_excel.py` | Worker path and welcome message semantics |
| `repositories_students.py` | Helper signatures allow no-tenant usage |
| `core/serializers.py` | `linkedStudents` read path for parent auth bootstrap |
| `permanently_delete_students` service graph | Destructive raw SQL across many domains, now behind one tenant-guarded service |

## Step Plan

### 1. Guard Before Editing Behavior

- Snapshot current responses for `/students/`, `/students/{id}/`,
  `/students/me/`, `/student/me/`, and `/core/me`.
- Add tenant isolation tests for cross-tenant student list/detail/update/delete.
- Add field propagation test: update phone/parent_phone/name/school through the
  canonical admin path, then assert all student read surfaces agree.
- Add legacy route usage scan for frontend and E2E.

### 2. Add Selectors

- Implement tenant-required selectors.
- Deleted-state must be explicit: active only, deleted only, or any.
- Replace low-risk read-only callers first.
- Keep repository wrappers until call sites are migrated.

Completion:

- No new call in touched files uses `Student.objects.filter(...)` directly.
- Selector tests prove tenant filter is mandatory.

### 3. Add Profile/Identity Service — [COMPLETED FOR TOUCHED WRITE PATHS]

- Implement `update_student_profile` with parent relink and OMR recompute.
- Use same service from `StudentViewSet.perform_update` and
  `StudentProfileView.patch`.
- Keep current response shapes while routing writes through the service.
- Add deprecation logging for `/students/me/` if it remains a compatibility
  surface.

Completion:

- Updating `parent_phone` through admin or student profile relinks parent the
  same way.
- Updating phone/parent phone recomputes OMR consistently.

### 4. Add Creation Service

- Implement one service for create with adapters for admin create, bulk JSON,
  registration approval, and import rows.
- Normalize User, Parent, Student, TenantMembership creation in one use-case.
- Do not change password/account recovery release behavior in this step.

Completion:

- Existing create endpoints pass contract tests.
- Duplicate and deleted-student conflict behavior is preserved or explicitly
  documented before changing.

### 5. Add Lifecycle Service — [PARTIAL]

- Implement soft delete and restore service. [DONE]
- Preserve existing side effects first: user active, membership, enrollment,
  clinic participant cancellation, notification event.
- Wrap permanent delete with a service entry and tenant assertions before moving
  raw SQL. [DONE service shell / TODO domain hooks and dry-run]

Completion:

- `destroy` and `bulk_delete` use same soft-delete service.
- `bulk_restore` and import restore use the same restore service or documented
  compatibility adapter.
- `bulk_permanent_delete`, `bulk_resolve_conflicts` delete, duplicate cleanup,
  and purge commands use the same permanent-delete service.

### 6. Frontend API Convergence

- Shared student DTO/mapper now lives in `src/shared/api/contracts/students`.
- Teacher student surfaces use the shared contract; auth signup still needs a
  dedicated cleanup pass before removing the admin compatibility facade.
- Keep UI behavior unchanged except where backend contract is fixed.

Completion:

- No touched teacher/student file imports `@admin/domains/students/*`; auth is
  tracked as remaining compatibility debt.
- Typecheck or generated contract catches response drift for touched endpoints.

## Required Tests

Backend:

- Student selector tenant-required tests.
- Student create contract tests for admin, registration approve, import row.
- Student profile update tests for parent relink and OMR recompute.
- Student soft delete/restore tests for User, TenantMembership, Enrollment,
  ClinicSessionParticipant, and messaging event.
- Contract snapshots for student read APIs.
- Tenant isolation tests for cross-tenant IDs and parent linked students.

Frontend:

- Mapper/type tests for `active/is_managed`, `noPhone/uses_identifier/omr_code`,
  school fields, and parent/student phone fields.
- Typecheck after contract migration.

E2E:

- Admin edits student profile; teacher detail, student profile, auth linked
  student, and messaging recipient preview reflect the same data.
- Parent switches children through `X-Student-Id` and cannot access another
  tenant's child.
- Legacy route scan fails on `/students/students/` unless explicitly exempted.

Visual QA:

- Only required for touched frontend screens: admin students home/detail/edit,
  teacher student detail, student profile, signup/recovery modal if touched.

## Rollback Plan

- Keep all existing URLs and serializers until Phase 1 tests pass.
- New selectors/services are additive. Rollback can route views back to previous
  inline logic while leaving tests/docs in place.
- Do not delete legacy routes in Phase 1.
- Do not run destructive data migrations.
- Permanent delete changes must keep the old raw SQL path behind a guarded
  compatibility function until domain hooks are proven.

## Completion Criteria

Phase 1 is done only when:

- one canonical student profile update is reflected in admin, teacher, student
  app, auth linkedStudents, and messaging recipient surfaces;
- student create/restore/delete use named services rather than view-local
  orchestration;
- tenant is mandatory in touched selectors/services;
- legacy paths have deprecation logging and usage detection;
- focused backend tests and the student propagation E2E pass;
- docs are updated with current canonical/deprecated paths.
