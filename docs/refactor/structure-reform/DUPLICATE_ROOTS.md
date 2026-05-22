# Duplicate Roots

**Status:** [VERIFIED] code/path audit plus [PROPOSED] canonical candidates  
**Captured:** 2026-05-22  
**Scope:** student-centered duplicate roots and adjacent high-risk paths

Risk scale:

- P0: tenant/data-loss/security risk
- P1: high user-visible drift or operational incident risk
- P2: medium drift or refactor blocker
- P3: cleanup/documentation risk

| Feature | Current paths | Actual callers/screens/APIs | Canonical candidate | Remove/integrate target | Risk | Tests needed |
|---|---|---|---|---|---|---|
| Student list/detail read | Backend `StudentViewSet.get_queryset`, `StudentDetailSerializer`; frontend admin `students.api.ts`; teacher `students/api.ts`; clinic/messaging/results direct reads | Admin students home/detail, teacher students list/detail, clinic add participant, messaging send, results grade pages | `students.selectors.list_students`, `get_student_detail`, `get_student_for_tenant` | Direct `Student.objects` reads in non-students domains; duplicated FE mappers | P1 | API contract snapshot, tenant isolation, admin/teacher/student profile visibility |
| Student profile read | `/students/{id}/`, `/students/me/`, `/student/me/`, `/core/me linkedStudents` | Admin/teacher detail, student profile, parent child switcher, auth bootstrap | `students.selectors.get_student_profile_context` | Manual DTO builders in `StudentProfileView` and `StudentViewSet.me` | P1 | Snapshot all four responses before convergence |
| Student create | `StudentViewSet.create`, `bulk_create`, `_approve_registration_request`, `get_or_create_student_for_lecture_enroll`, Excel worker, E2E direct API setup | Admin create modal, teacher create sheet, signup approval, lecture enroll Excel, student Excel import, E2E fixtures | `students.services.create_student_for_tenant` plus specialized adapters | View-level user/parent/membership creation logic | P1 | Create contract, duplicate rejection, parent link, membership, welcome event tests |
| Student restore | `bulk_restore`, `bulk_resolve_conflicts`, `lecture_enroll` deleted-student restore, deleted duplicate fix | Deleted student page, conflict resolver, lecture/enrollment import | `students.services.restore_student` | Local unmangle/reactivate/relink implementations | P0 | Restore identity collision, tenant isolation, user/membership/enrollment behavior |
| Student update | `StudentViewSet.perform_update`, `StudentViewSet.me`, `StudentProfileView.patch`, teacher `updateStudent`, admin `updateStudent` | Admin edit modal, teacher detail, student profile page | `students.services.update_student_profile` | Direct `student.save()` update branches in views | P1 | Parent relink, OMR recompute, school mapping, profile photo, id collision |
| Student schedule hidden state | `StudentSessionClearPastView`, `StudentSessionHideView`, `StudentSessionUnhideView` write `Student.schedule_hidden_*` | Student sessions page | `students.services.update_schedule_visibility` or student-app policy wrapper | Inline mutation in student-app session views | P2 | Student/parent tenant and child selection tests |
| Student soft delete | `StudentViewSet.destroy`, `bulk_delete` | Admin/teacher delete, E2E cleanup | `students.services.soft_delete_student` | Duplicated user/membership/enrollment/clinic side effects | P0 | Soft delete state machine, notification event, all related surfaces hidden |
| Student permanent delete | `bulk_permanent_delete`, purge/deleted duplicate commands | Deleted students admin cleanup, E2E cleanup | `students.services.permanently_delete_students` with domain hooks/events | Raw SQL in view; direct cross-domain table deletes | P0 | Dry-run, tenant isolation, cross-domain referential cleanup snapshot |
| Parent link/account | `ensure_parent_for_student`, registration approve, create/bulk create, restore, profile update missing relink | Student create/edit, signup approval, parent app, account recovery | `parents.services.ensure_parent_for_student` called only by student service | Per-view parent relink logic; missing relink in `/student/me` | P1 | Parent phone change updates relation and linkedStudents |
| Password/account recovery | `/auth/account-recovery/dispatch/`, `/students/password_reset_send/`, `/students/password_find/request/`, `/students/password_find/verify/`, `/students/send_existing_credentials/` | Auth recovery UI, admin password modal, teacher password modal, signup duplicate flow, E2E password reset | Keep current release path stable, then canonical `account_recovery` dispatch facade | Legacy student password endpoints should get deprecation logs before removal | P1 | Existing release tests plus legacy deprecation detection |
| Registration request approve | `RegistrationRequestViewSet.approve`, `bulk_approve`, auto-approve inside create | Signup modal, admin requests page, teacher comms notifications | `students.services.approve_registration_request` | `_approve_registration_request` view helper with create logic | P1 | Approval contract, auto-approve, duplicate, parent password payload |
| Excel student import | `/students/bulk_create_from_excel/`, `bulk_from_excel.process_student_bulk_create_from_excel`, `lecture_enroll` helper | Admin/teacher Excel upload, AI job status | `students.services.import_students_from_rows` called by worker/API | View dispatch plus worker logic split | P1 | Job payload contract, R2 path, row validation, duplicate/restore |
| Enrollment matrix | `/students/{id}/enrollment-matrix/`, `/toggle/` | Admin student enrollment matrix drawer | `[COMPLETED] enrollment.selectors` + `enrollment.services.lifecycle` public API called by students facade | Student domain view no longer mutates enrollment/session state directly; legacy URL remains as facade | P2 | `[DONE]` tenant isolation and session/exam/homework scope tests |
| Exam/homework assignment roster | `ExamEnrollmentManageView`, `HomeworkEnrollmentManageView`, `HomeworkAssignmentManageView` each built valid roster independently | Exam enrollment modal, homework enrollment/assignment screens | `[COMPLETED] enrollment.selectors.active_session_enrollments_for_session` | Per-view SessionEnrollment filtering with missing tenant/status/deleted checks | P1 | `[DONE]` cross-tenant corrupted row exclusion and inactive enrollment rejection |
| Attendance roster create | `AttendanceViewSet.bulk_create` queries Student directly | Attendance/lecture sessions flows | `attendance.services.create_roster` using students selector | Direct `Student.objects.filter` in attendance view | P1 | Cross-tenant student IDs rejected, roster idempotency |
| Results student grades | `/results/admin/student-grades/` validates Student directly | Admin/teacher student detail grades | `results.selectors.get_student_grades(tenant, student_id)` using students selector | Result view direct Student existence check | P2 | Student missing/deleted/cross-tenant contract |
| Clinic participants | Clinic serializers/views import Student and set querysets | Clinic operations console, add participant | `clinic.services.add_participant` with students selector | Serializer-owned Student querysets/business validation | P1 | Add participant cross-tenant and deleted-student rejection |
| Messaging recipients | `SendMessageView`, `notification_dispatch` direct Student query | Messaging send, event notifications | `messaging.services.resolve_recipients` with students selector DTO | Direct Student field reads in messaging layer | P1 | Parent/student target selection, missing phone, tenant isolation |
| Frontend student DTO mapping | Admin `mapStudent`, teacher import of admin mapper/types, student `MyProfile`, auth signup imports admin API | Admin students, teacher students, student profile, signup modal | `src/shared/api/contracts/students` after OpenAPI schema | Role-app imports of `@admin/domains/students/*` | P2 | Typecheck, route render, contract snapshots |
| Frontend enrollment API wrappers | `[COMPLETED]` admin lectures, exams, homework, clinic tab, and teacher score entry previously depended on role-local wrappers for `/enrollments/session-enrollments/` | Lecture score roster, exam creation/enrollment panel, homework assignment/enrollment panel, session clinic tab, teacher mobile score entry | `frontend/src/shared/api/contracts/sessionEnrollments.ts` with admin compatibility facades | Lectures/exams/homework session enrollment API files remain compatibility facades; active call sites now use shared contract where cross-domain import was unnecessary | P2 | `[DONE]` focused ESLint, frontend typecheck, boundary snapshot 43→41 |
| Legacy/wrong E2E route | `[COMPLETED] e2e/admin/dnb-lectures-sessions.spec.ts` no longer calls `/api/v1/students/students/` | DNB lecture/session E2E setup/cleanup | Current `/api/v1/students/` or shared E2E data helper | Wrong route removed from this helper; CI guard blocks reintroduction of `students/students`, `lectures/enrollments`, and direct enrollment-create routes in tracked source/E2E files | P2 | `[DONE]` `pnpm guard:legacy-api` |
| Student field naming | Backend `is_managed`, `uses_identifier`, `no_phone`, `omr_code`; frontend `active`, `noPhone`, synthetic phone, `studentPhone` | Admin/teacher create/edit; student/profile | Generated contract plus explicit UI form mapper | Implicit field semantics in each API client | P1 | Contract snapshot and mapper unit tests |

## Canonicalization Order

1. Introduce selectors for reads and replace non-students direct reads only when
   tests cover the caller.
2. Introduce services for create/update/lifecycle and route existing views
   through them without changing URLs.
3. Add deprecation logging for legacy paths before removal.
4. Converge frontend role-app APIs onto a shared contract after backend schema
   snapshots exist.
5. Remove legacy helpers only after route usage detection proves no caller
   remains.

## Required Explicit Findings

- Student creation/restoration is actually 6+ roots.
- Student profile update is actually 3 primary roots plus schedule visibility
  mutations.
- Password/account recovery is actually 4 API roots and must not be mixed with
  the current release while Phase 1 student canonicalization starts.
- The E2E lecture/session path used `/api/v1/students/students/`, not the current
  `/api/v1/students/`; the known tracked caller has been migrated.
- `noPhone/uses_identifier/omr_code`, `active/is_managed`, and parent initial
  password have semantic drift across backend and frontend.

## Implemented Convergence Notes

- 2026-05-22: Phase 2 enrollment matrix and assignment roster writes/read
  scope now converge through `apps.domains.enrollment.selectors` and
  `apps.domains.enrollment.services.lifecycle`.
- 2026-05-22: Phase 3 automatic notification queue payloads carry
  source/use-case/domain object/actor metadata for operational tracing.
- 2026-05-22: Phase 4 AI job gateway rejects missing tenant/source and
  payload tenant mismatch before SQS publish.
- 2026-05-22: Phase 5 first cleanup removed the known
  `/api/v1/students/students/` E2E helper route.
- 2026-05-22: Phase 5 frontend enrollment API cleanup created a canonical
  `app_admin/domains/enrollment/api/enrollments.ts` client. Lectures, exams,
  and homework session-enrollment wrappers now delegate to it instead of each
  normalizing the same endpoint independently.
- 2026-05-22: Session-enrollment fetch/bulk contract moved to
  `src/shared/api/contracts/sessionEnrollments.ts`. Admin enrollment remains a
  compatibility facade, lecture scores/exam creation/session clinic/teacher
  mobile score entry call the shared contract directly, and the previous exams
  `404/501 -> []` compatibility is preserved in the shared contract.
- 2026-05-22: Frontend CI runs `pnpm guard:legacy-api` to fail on reintroduced
  legacy `/students/students/`, `/lectures/enrollments/`, or direct
  `/enrollments/` create calls in tracked `src`/`e2e` TypeScript/JavaScript.
- 2026-05-22: Frontend shared purity slice moved theme, responsive view,
  clinic-target, video status/workbox, and session-progress contracts under
  `src/shared/*`. Admin role paths remain compatibility facades where needed;
  `shared -> app_*` imports became 0 and cross-app/admin role imports became 37.
- 2026-05-22: Operational notification counts moved to shared notification
  contracts/hooks. Admin notification modules are compatibility facades, teacher
  surfaces use `useTeacherPendingCounts`, and cross-app/admin role imports are
  now 30.
