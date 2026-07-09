# Student Domain Core SSOT

**Status:** Active
**Last checked:** 2026-06-07 KST
**Truth basis:** code inspection of `apps/domains/students/`, `apps/core/views/account_recovery.py`, `apps/core/services/password.py`, `apps/domains/results/services/submission_scope_guard.py`, `apps/domains/results/services/student_result_service.py`, and frontend shared student contracts.

This document is the integration SSOT for the student domain. More specific
documents still own their detailed contracts:

- creation and import: `student-creation.md`
- deletion/restore/permanent delete: `student-lifecycle.md`
- login ID and password recovery: `account-recovery.md`
- parent account graph: `parent-account.md`
- OMR scoring: `omr.md`
- messaging and Alimtalk: `messaging-alimtalk.md`

## 0. Product Rule

Student is the product spine. A feature that displays, grades, notifies,
assigns, books, or reports learning content must treat the student account
graph and tenant-scoped enrollment graph as first-class constraints.

Broad promotion or expansion launch must not proceed on a "screen loads" signal
alone. The release gate must prove the chain from student identity to the
consumer role that sees the final state.

## 1. Canonical Student Graph

The durable student graph is:

```text
Tenant
  -> User
  -> Student
  -> TenantMembership(role="student")
  -> Parent link, when parent_phone is present
```

Canonical creator:

- `apps/domains/students/services/creation.py::create_student_account()`

Owned by the creator:

- ensure or link the parent account;
- create the student user;
- create the student row;
- create or reactivate student tenant membership;
- return the parent password phrase for notices.

Not owned by the creator:

- duplicate/deleted-student decisions;
- serializer/API response shape;
- Excel/R2/AI worker dispatch;
- Alimtalk dispatch;
- registration status transition.

## 2. Identity Invariants

Canonical identity helper:

- `apps/domains/students/services/identity.py`

Required invariants:

- `tenant` must be resolved by the caller. No tenant fallback.
- active student means `Student.deleted_at IS NULL`.
- `Student.user` is required.
- `Student.ps_number` is tenant-unique and is the student login display ID.
- internal username mirrors `ps_number` through `user_internal_username(tenant, ps_number)`.
- student phone is optional; parent phone is required on creation/import/signup.
- phone fields are normalized to numeric `010XXXXXXXX` 11-digit strings.
- malformed student phone is rejected. Do not silently convert it to identifier mode.
- if student phone exists, `omr_code` is the last 8 digits of student phone.
- if student phone is absent, `omr_code` is the last 8 digits of parent phone.
- no fake student phone is created only to satisfy downstream code.
- `uses_identifier=True` means the student has no student phone and is identified by the account/OMR identifier flow.

Current canonical entry points:

| Flow | Canonical path |
|---|---|
| admin single create | `StudentCreateSerializer` -> `create_student_account()` |
| JSON bulk create | `import_students_from_rows()` |
| Excel/worker import | `ExcelParsingService` -> `import_students_from_rows()` |
| lecture/enrollment Excel import | `resolve_student_import_row()` |
| signup approval | `approve_registration_request()` -> `create_student_account(password_hash=...)` |
| admin/student profile write | `update_student_profile()` |
| deleted conflict restore/delete | `restore_student()` / `permanently_delete_students()` through import conflict resolver |

## 3. Signup, ID Recovery, Password Recovery

Detailed SSOT: `account-recovery.md` and `student-creation.md`.

Current rules:

- signup request stores password hash only; `initial_password_plain` must remain empty.
- signup approval uses the original password hash and tells the student
  "가입 신청 시 입력한 비밀번호" instead of exposing plaintext.
- signup approval status transition and student creation are atomic in
  `approve_registration_request()`.
- approval Alimtalk failure does not hide an already committed approval.
- public ID/password recovery uses `/api/v1/auth/account-recovery/dispatch/`.
- legacy public OTP password-find endpoints are sealed with 410 Gone.
- public password recovery creates `PendingPasswordReset` and changes the real
  password only when the temporary password is used to log in.
- unknown, ambiguous, and successful public recovery responses must be generic.
- public recovery sends only to the verified phone supplied by the user.
- staff/teacher password reset through `/students/password_reset_send/` is a
  privileged path:
  - authenticated active owner/admin/teacher/staff membership is required for
    `temp_password`; `skip_notify` is accepted only as legacy input and does
    not suppress SYSTEM_AUTO account notices;
  - student target may resolve by `student_ps_number` or verified student phone;
  - parent target resolves by student name + parent phone;
  - password changes immediately;
  - pending reset is cleared;
  - Alimtalk delivery failure rolls back the password and pending-reset state.
- password minimum length remains 4. Do not raise it.
- automatic temporary password generation is 6 numeric digits for user handling,
  not a minimum-length policy change.

## 4. Alimtalk Boundaries

Detailed SSOT: `messaging-alimtalk.md`.

Student account Alimtalk is system-critical but still fail-closed:

- `send_alimtalk_via_owner()` is the canonical account-notification dispatcher.
- account triggers use exact approved owner templates.
- no SMS fallback.
- `password_reset_*` must not fall back to `registration_approved_*`.
- any student/parent account ID or password change sends a SYSTEM_AUTO account
  notice. ID-only changes, parent phone relinks, and first-time student phone
  registration use `registration_approved_*` with password phrase
  `변경되지 않음`.
- welcome/approval notices use service-returned parent password phrases:
  - new parent account: parent initial password phrase;
  - existing parent account: `변경되지 않음`.
- account notification logs are linked back through `source_tenant_id`,
  `target_type="account"`, and stable target IDs.
- student detail UI may show account-notification status metadata, never the
  message body or temporary password.

## 5. Student-Linked Content Rule

Any feature connected to learning content must resolve student scope through the
tenant and the appropriate roster object. Direct student lookup is not enough
when the feature is tied to a class/session/exam/homework/clinic context.

Required chain:

```text
Tenant
  -> active Student
  -> active Enrollment
  -> SessionEnrollment / ExamEnrollment / HomeworkEnrollment / Clinic participant
  -> Submission / Result / Achievement / Notification / Student app projection
```

Canonical active-enrollment selector for student-facing projections:

- `apps/domains/enrollment/selectors.py::active_enrollments_for_student()`
- `apps/domains/enrollment/selectors.py::active_enrollments_for_students()`
- `apps/domains/enrollment/selectors.py::active_enrollment_ids_for_student()`

Student-facing exam lists/detail/submission, exam result detail, grades summary,
homework summary, video visibility/progress, dashboard scope, clinic remediation,
wrong-note/PDF, exam-attempt history, attendance summaries, schedule mutations,
and future linked-content reads must use this selector or a narrower selector
with the same tenant + active student + active enrollment constraints.
Public video's synthetic system lecture is the only intentional exception; it
must call the same selector with `include_system=True` and keep the exception
local to public-video accounting.

Rules:

- no cross-tenant fallback;
- no deleted-student fallback;
- no inactive enrollment fallback for scored/submitted content;
- when `student_id` and `enrollment_id` are both present, cross-check both;
- when only `student_id` is present and a roster context exists, resolve the
  active enrollment with explicit ordering or fail with a user-visible error;
- OMR candidate matching is same-tenant, active roster only;
- ambiguous OMR/phone/name matches must go to manual review, not silent choice;
- student-facing projections must be verified from the student role after admin
  or teacher writes the state.

## 6. Connected Domains

| Domain | Student contract |
|---|---|
| OMR automatic grading | candidate set is same-tenant active roster; identifier is phone/parent-phone last 8 digits; unmatched/ambiguous scans remain reviewable facts |
| Results/exam scores | submission, exam, enrollment, and tenant must match before score/result writes |
| Clinic | clinic target and remediation state must resolve through enrollment/session context |
| Homework | assignment/submission rows must carry tenant-scoped enrollment identity |
| Attendance | attendance status that affects secession/enrollment must call the lifecycle path, not mutate student rows directly |
| Video/progress | student visibility and progress must use tenant-scoped enrollment/session access |
| QnA/community/counseling | student author/target must be tenant-scoped and not inferred from display name |
| Messaging | recipients come from the verified student/parent phone in the resolved student graph |

## 7. Minimum Change Gate

When a change touches any of these surfaces, run the smallest focused set that
proves the touched path, then broaden when the behavior crosses domains.

Always for student identity/account changes:

```powershell
cd C:\academy\backend
python -m pytest apps\domains\students\tests\test_student_identity_convergence.py apps\domains\students\tests\test_registration_password_safety.py apps\domains\students\tests\test_password_reset_safety.py apps\domains\students\tests\test_account_recovery.py -v --tb=short -x
python manage.py check --settings apps.api.config.settings.test
python manage.py makemigrations --check --dry-run --settings apps.api.config.settings.test
```

Add for OMR/results/submission changes:

```powershell
cd C:\academy\backend
python -m pytest apps\domains\student_app\tests\test_grades_summary_homework.py apps\domains\results\tests\test_submission_scope_guard.py apps\support\omr\tests\test_candidate_matching.py -v --tb=short -x
```

Add for student video/progress access changes:

```powershell
cd C:\academy\backend
python -m pytest tests\test_student_video_progress_enrollment_resolution.py -v --tb=short -x
```

Add for frontend account/student UI changes:

```powershell
cd C:\academy\frontend
pnpm typecheck
pnpm guard:legacy-api
pnpm build
pnpm exec playwright test e2e\auth\account-recovery-modal.spec.ts --reporter=list
```

Launch-readiness and broader real-use gates are tracked in
`../refactor/student-domain-launch-readiness.md`.

## 8. Do Not

- do not add a second student identity helper in serializers/views/frontend only;
- do not store plaintext signup or temporary passwords;
- do not reset a public user's actual password before delivery/activation;
- do not use a stored different phone when the public user proved another phone;
- do not send account notices through SMS fallback;
- do not expose account existence in public recovery responses;
- do not let OMR, clinic, homework, results, or QnA choose a student by name alone;
- do not treat admin-side success as complete until the student/parent-facing
  projection is checked when that projection exists.
