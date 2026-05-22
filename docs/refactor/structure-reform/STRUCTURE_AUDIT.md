# Structure Reform Audit

**Status:** [VERIFIED] codebase audit with [INFERRED] refactor risk notes  
**Captured:** 2026-05-22  
**Scope:** backend `C:\academy\backend`, frontend `C:\academy\frontend`  
**Excluded:** current account-recovery/password/Alimtalk release implementation changes, `dnfm`, `dnfm-group`

This audit is Phase 0 material. It does not approve a large rewrite. It records
where feature entrypoints currently split, where tenant/data integrity is not
structurally enforced, and which small refactor should start Phase 1.

## 1. Current Structure Summary

[VERIFIED]

- `C:\academy` is a workspace, not a git repository. `backend/` and `frontend/`
  are separate git repositories.
- Backend root API routing lives in `apps/api/v1/urls.py`.
- Backend product domains currently live under `apps/domains/`.
- Frontend role surfaces live under `src/app_admin`, `src/app_teacher`,
  `src/app_student`, plus `auth`, `core`, `shared`, `landing`.
- Current refactor inventory measured 27 backend domain directories,
  104 backend cross-domain imports, 645 backend cross-domain internal imports,
  1 frontend cross-app import, and 0 `shared -> app_*` imports.

Backend API prefixes relevant to this audit:

| Prefix | Include | Notes |
|---|---|---|
| `/students/` | `apps.domains.students.urls` | Admin/teacher student management, registration, password/reset helpers |
| `/student/` | `apps.domains.student_app.urls` | Student/parent app BFF profile, sessions, results, video, fees |
| `/lectures/` | `apps.domains.lectures.urls` and `apps.domains.attendance.urls` | Shared prefix for lecture and attendance endpoints |
| `/results/` | `apps.domains.results.urls` | Admin/student grade surfaces |
| `/homework/` | `apps.domains.homework.urls` | Homework policy/enrollment/assignment and homework score route import |
| `/homeworks/` | `apps.domains.homework_results.urls` | Homework result surface |
| `/clinic/` | `apps.domains.clinic.urls` | Clinic sessions, participants, submissions |
| `/messaging/` | `apps.domains.messaging.urls` | Templates, config, logs, send, previews |
| `/auth/` | `apps.core.auth_urls` | Auth and current account-recovery dispatch |
| `/jobs/` | `apps.domains.ai.urls` | AI job status/queue API |

## 2. Student Domain Canonical Source

[VERIFIED]

The canonical persisted student source is `apps.domains.students.models.Student`.
It owns:

- tenant membership context through `tenant`, `user`, and `TenantMembership`;
- identity fields: `ps_number`, `omr_code`, `name`, `phone`, `parent_phone`;
- parent relation: `parent`;
- school/profile fields: gender, grade, school type and school names/classes,
  address, memo, profile photo keys;
- lifecycle fields: `is_managed`, `deleted_at`;
- student-app schedule visibility fields: `schedule_hidden_before`,
  `schedule_hidden_ids`;
- tags through `Tag` and the join table.

[INFERRED]

There is no single canonical read selector or write use-case today. The model is
the database source, but behavior is split among views, serializers, service
helpers, repository wrappers, frontend mapping functions, and E2E helpers.
Therefore a patch to a student field is not guaranteed to reach every surface.

## 3. Student Data Read Paths

[VERIFIED]

| Surface | Current path | Current implementation |
|---|---|---|
| Admin student list/detail | `/api/v1/students/`, `/api/v1/students/{id}/` | `StudentViewSet.get_queryset` plus `StudentSerializer` / `StudentDetailSerializer` |
| Teacher student list/detail | same `/students/` API | `src/app_teacher/domains/students/api.ts`, using `src/shared/api/contracts/students` |
| Student/parent profile | `/api/v1/student/me/` | `StudentProfileView`, manual response DTO |
| Student legacy self profile | `/api/v1/students/me/` | `StudentViewSet.me`, separate GET/PATCH shape |
| Auth current user | `/api/v1/core/me/` | `UserSerializer.linkedStudents`, reads `parent.students` |
| Student app sessions/results/dashboard | `/api/v1/student/*` | BFF views aggregate enrollment, attendance, exams, results, video, fees |
| Messaging send targets | `/api/v1/messaging/send/` | `SendMessageView`, direct `Student.objects.filter(...)` |
| Clinic/attendance/results helpers | domain-specific endpoints | Direct Student import/query in multiple domains |

Important finding: this feature is actually at least 7 read roots when "student
identity/profile as shown to users" is counted across admin, teacher, student
app, auth, messaging, clinic/attendance/results, and E2E helper paths.

## 4. Student Data Write Paths

[VERIFIED]

| Feature | Current write roots | Evidence |
|---|---:|---|
| Single student create | 1 primary, plus duplicates | `StudentViewSet.create` creates User, Student, Parent link, TenantMembership, welcome message |
| Bulk JSON create | 1 duplicate root | `StudentViewSet.bulk_create` repeats ps_number, OMR, parent, user, membership logic |
| Excel/import create | 2 roots | `bulk_create_from_excel` dispatches worker; `services/bulk_from_excel.py` calls `lecture_enroll` |
| Lecture enrollment create/restore | 1 root | `get_or_create_student_for_lecture_enroll` creates/restores Student and User |
| Registration approve | 1 root | `_approve_registration_request` creates User, Student, Parent, TenantMembership |
| Admin/student profile update | 3 roots | `StudentViewSet.perform_update`, `StudentViewSet.me`, `StudentProfileView.patch` |
| Schedule visibility | 3 action roots | `StudentSessionClearPastView`, `StudentSessionHideView`, `StudentSessionUnhideView` mutate Student fields |
| Soft delete / restore / permanent delete | 6 roots | `destroy`, `bulk_delete`, `bulk_restore`, `bulk_permanent_delete`, duplicate fix, purge command |
| Password/account recovery | 4 API roots | `/auth/account-recovery/dispatch/`, `/students/password_reset_send/`, `/students/password_find/*`, `/students/send_existing_credentials/` |

Required report statement: this function is actually 6+ roots for student
creation/restoration and 4 roots for password/account recovery.

## 5. Why Student Patches Drift

[VERIFIED]

- `StudentProfileView.patch` updates `parent_phone` directly but does not relink
  `Parent`; `StudentViewSet.perform_update` relinks parent when `parent_phone`
  changes.
- `StudentProfileView.patch` updates `phone` / `parent_phone` but does not
  recompute `omr_code`; `StudentUpdateSerializer.validate` recomputes OMR.
- `StudentViewSet.me` has a third self-profile PATCH flow and performs username,
  password, profile photo, school field, OMR, and parent relink logic separately.
- `Student.save()` updates `User.username` and inventory `student_ps` when
  `ps_number` changes. This means the students model directly modifies another
  domain's internal data.
- Frontend admin `mapStudent` computes `active` from `is_managed`, but deletion,
  user active state, enrollment status, and `is_managed` are different backend
  states.
- Frontend admin `updateStudent` with `noPhone` sends a synthetic phone and
  `omr_code`, while backend treats `omr_code` as read-only on update and computes
  it from phone/parent phone.
- Parent password meaning drift exists: `parents.services` creates parent initial
  password from the last 4 digits, while registration/bulk welcome messaging can
  still report `"0000"`.

Required report statement: this field is used as backend meaning A and frontend
meaning B in multiple places. `uses_identifier/no_phone/omr_code` is the clearest
case: backend uses it to determine identity and OMR derivation, while frontend
uses `noPhone` to synthesize a phone-like value and attempts to send `omr_code`.

## 6. Duplicate Root And Legacy Risks

[VERIFIED]

- Admin student API is now a compatibility facade over the shared student
  contract. Teacher student surfaces use the shared contract, while some auth
  and admin-internal callers still go through the admin facade.
- `auth/pages/SignupModal.tsx` imports admin student API internals for public
  signup helpers.
- `auth/api/recovery.api.ts` calls `/auth/account-recovery/dispatch/`, while
  admin/teacher/public student APIs still call `/students/password_*` or
  `/students/send_existing_credentials/`.
- E2E `e2e/admin/dnb-lectures-sessions.spec.ts` calls
  `/api/v1/students/students/`, while the deployed current route is
  `/api/v1/students/`.

Required report statement: this screen/test is not using the latest API. The E2E
lecture/session helper uses a legacy or wrong `/students/students/` route and
must be detected before refactoring deletes or renames any student path.

## 7. Tenant And Data Integrity Risks

[VERIFIED]

High-risk patterns found:

- `academy/adapters/db/django/repositories_students.py` contains helpers that can
  run without tenant, including `tag_all(tenant=None)`,
  `user_create_user(tenant=None)`, `tag_get(tenant=None)`,
  `student_filter_tenant_deleted()`, `user_filter_username(username)`,
  `user_filter_phone(phone)`, `user_filter_phone_exists(phone, tenant=None)`,
  `student_filter_deleted_dup_groups()`, and
  `student_filter_deleted_before_cutoff(cutoff)`.
- `apps/core/serializers.py` reads `parent.students.filter(...)` without an
  explicit tenant filter for `linkedStudents`; it relies on the parent relation
  invariant rather than a tenant-scoped selector.
- `StudentViewSet.bulk_permanent_delete` uses raw SQL to delete/update data in
  results, submissions, homework, attendance, enrollment, exams, video, progress,
  clinic, community, students, core membership, and accounts user tables.
- Some raw delete/update clauses rely on already tenant-scoped student/user IDs
  rather than repeating tenant in every table operation.
- Management commands for deleted student duplicate cleanup and purge operate on
  broad querysets and need explicit tenant/run-mode guardrails.

Required report statement: tenant filter is not structurally guaranteed in these
helper paths. Some callers pass tenant correctly, but the helper signatures still
allow unsafe no-tenant usage.

## 8. Domain Boundary Collapse Points

[VERIFIED]

- Students model save logic imports and updates inventory models.
- Student permanent delete directly edits internal tables across assessment,
  academics, clinic, media, community, and core.
- Attendance, clinic, community, fees, messaging, results, and enrollment import
  or query `Student` directly in views/serializers/services.
- `homework.views.__init__` imports `HomeworkScoreViewSet` from
  `homework_results`, so two homework-related roots are coupled at URL/export
  level.
- `student_app` is a BFF surface but lives as a domain and performs aggregation
  across sessions, attendance, exams, results, video, and fees.
- Frontend auth and some admin-internal surfaces still import admin student
  compatibility paths. Teacher student/storage and student inventory surfaces
  now use shared contracts directly.

Required report statement: these domains are directly modifying or reading other
domains' internal models/tables rather than going through public selectors,
services, or events.

## 9. Bug Traceability Problems

[INFERRED from verified roots]

- Student lifecycle state is spread across `Student.deleted_at`,
  `Student.is_managed`, `User.is_active`, `TenantMembership.is_active`,
  `Enrollment.status`, clinic participant status, and messaging side effects.
- State transitions are not explicit use-cases. They are implemented in
  `destroy`, `bulk_delete`, `bulk_restore`, `bulk_permanent_delete`, registration
  approve, Excel restore, and management commands.
- Notifications are fired inside view/service flows rather than through a
  durable domain event/outbox contract.
- Logs exist in some destructive paths, but a uniform
  `tenant/actor/source/use_case/correlation_id` convention is not present across
  student create/update/delete/recover/import.
- Current tests cover important patches, but they mostly target specific
  endpoints. They do not yet assert that one canonical student update is visible
  through admin, teacher, student app, auth linked students, messaging recipient,
  and E2E UI surfaces.

## 10. Implemented Reform Slices

[COMPLETED 2026-05-22]

- Phase 1 student profile canonicalization introduced tenant-scoped student
  selectors and `students.services.profile.update_student_profile`.
- Phase 2 enrollment canonicalization introduced
  `apps.domains.enrollment.selectors` and
  `apps.domains.enrollment.services.lifecycle`.
- Student enrollment matrix still uses the existing student URL, but the view is
  now a facade over the enrollment domain. Session/exam/homework toggles are
  validated against tenant + student + lecture + active enrollment in one use
  case.
- Exam/homework assignment screens now use the enrollment active-session roster
  selector instead of recomputing roster scope independently.
- Phase 3 notification queue payloads now include source/use-case/domain object
  metadata; attendance passes actor metadata for status-change sends.
- Phase 4 `dispatch_job` now blocks missing tenant/source and payload
  tenant-mismatch before creating an AI job.
- Phase 5 frontend shared-contract cleanup moved community contracts, patch
  notes data, video access/rule contracts, reusable video thumbnail UI,
  lecture/session attendance API, storage/inventory API, student API contracts,
  student Excel utilities, fees API/status contracts, the tools timer download
  contract, the exam enrollment contract, the tenant info contract, and
  submissions API/types out of admin internals. Frontend cross-app/admin role
  imports are now 1.

## 11. Phase 1 Recommendation

[COMPLETED INITIAL SLICE]

Phase 1 should start with `students` canonical read/write path, not with file
movement:

1. Add `students/selectors.py` for tenant-scoped reads.
2. Add `students/services.py` or a small `students/services/*` set for
   create/update/profile/lifecycle use-cases.
3. Route existing views through the new services without changing URLs.
4. Add deprecation logging around legacy/self/profile/password helper paths that
   are not removed yet.
5. Move frontend admin/teacher/student/auth API calls toward a shared student
   contract after backend contract snapshots exist.

This area is high-value because student identity/profile is the source used by
attendance, grades, clinic, messaging, storage, signup, and student app UX. It
is also bounded enough to start with tests and compatibility layers rather than
physical package moves.
