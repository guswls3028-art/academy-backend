# Student Domain Core SSOT

**Status:** Active
**Last checked:** 2026-08-23 KST
**Truth basis:** code inspection of `apps/domains/students/`, `apps/core/views/account_recovery.py`, `apps/core/services/password.py`, `apps/domains/results/services/submission_scope_guard.py`, `apps/domains/results/services/student_result_service.py`, and frontend shared student contracts.

This document is the integration SSOT for the student domain. More specific
documents still own their detailed contracts:

- creation and import: `student-creation.md`
- staff support preview and activity evidence: `student-support-audit.md`
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
- `Student.ps_number` and the inventory copies that scope student files use the
  same 50-character storage boundary; a valid student identity must not fail
  when it is mirrored into inventory metadata.
- internal username mirrors `ps_number` through `user_internal_username(tenant, ps_number)`.
- student phone is optional; parent phone is required on creation/import/signup.
- phone fields are normalized to numeric `010XXXXXXXX` 11-digit strings.
- `User.phone` mirrors `Student.phone`; profile changes update or clear both so
  account and notification paths cannot retain a stale student recipient.
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

New-student JSON/Excel import resolves each row inside the requested tenant in
this order:

1. A non-empty normalized student phone that matches exactly one active
   `Student` resolves that existing student.
2. Otherwise, an exact name plus normalized parent phone that matches exactly
   one active `Student` resolves that existing student. The name is opaque:
   suffixes and markers such as `김지우a`, `김지우b`, `김지우1`, `김지우2`,
   or parentheses are ordinary name text and are neither stripped nor folded.
3. The existing deleted-student name-plus-parent restore policy applies only
   to a unique deleted candidate. A deleted phone collision that is not that
   restore candidate remains an explicit conflict.
4. Only when those same-tenant candidates are absent is a new account graph
   created.

Multiple candidates at any lookup boundary fail the row closed instead of
choosing the first record. No cross-tenant fallback exists. Resolving an active
duplicate never rewrites the existing student's name, phone, parent phone,
login ID, password, token version, or pending account notice. A parent phone is
required but may be shared by siblings or twins; the student phone is optional.
The worker result must expose every source row as created, duplicate, restored,
or failed so a partial result cannot look like silent omission.
`created` remains the backward-compatible count, while `created_rows`,
`duplicates`, `restored`, and `failed` carry the Excel row and opaque student
name for the staff result dialog. Created/duplicate/restored rows may also carry
the same-tenant student ID for internal navigation, but the browser's persisted
result projection omits IDs, phones, and credentials. Known validation failures
include an allowlisted `reason_code` and user-safe message. Unexpected exceptions
are logged with their internal detail but return only the generic
`processing_error` reason; raw exception text must never enter the job result.

Student list search keeps its queryset tenant-scoped before applying filters.
A complete `010` mobile number in the general search or explicit student/parent
phone filter is compared exactly after removing presentation separators from
both the query and stored fields. Thus hyphenated and digit-only forms are
equivalent without introducing a normalized substring or cross-tenant fallback.
Non-phone general search continues to use the existing name, PS/OMR, school,
and major text fields.

Student and enrollment Excel uploads accept only the `.xlsx` extension. The
API validates a non-empty bounded upload, a supported browser MIME (including
Windows Hancom HCell's `application/haansoftxlsx`, empty MIME, and generic
`application/octet-stream`), the ZIP signature, `[Content_Types].xml`, and
`xl/workbook.xml` before R2 upload. MIME is client metadata and never replaces
the file-content checks. A renamed file, unrelated MIME, disguised ZIP, or
corrupt workbook fails closed, and the worker parser remains the final workbook
and row-validation boundary.

Student app profile photos use only the tenant-scoped R2 key returned by
`profile_photo_key(tenant_id, student_id, unique_id, ext)` as the readable
production state. An R2 upload or DB key-save failure returns retryable 503 and
must not fall back to a local `ImageField` that production cannot serve. After
the new key is saved, the exact replaced R2 object is deleted best-effort; a DB
save failure cleans the newly uploaded key best-effort and preserves the old DB
key. Invalid content type, image magic bytes, or files larger than 10 MiB are
rejected before upload.

## 2.1 Tenant Custom Student Fields

Teacher-specific profile columns are a tenant-scoped extension of the canonical
`Student` row, not a second student identity or profile table.

- `StudentCustomFieldDefinition` owns each tenant's label, stable generated key,
  value type, Excel aliases/options, order, and active state.
- `Student.custom_fields` stores values by the stable definition key. Display
  label changes therefore do not migrate or orphan student values.
- definitions and values are always resolved through `request.tenant`; a key
  from another tenant or an unknown/inactive key is rejected on write.
- supported value types are `text`, `number`, `date`, and `select`.
- deactivation is non-destructive. It hides the field from active forms and
  tables but never removes definitions or existing student values. Reactivation
  exposes the preserved values again.
- profile updates merge submitted active custom values into the existing JSON
  object. Core fields and inactive custom values are preserved.
- canonical core Excel headers keep precedence. Unknown Excel headers are
  preserved by `ExcelParsingService` and the student import service maps them
  only when they match an active definition label or alias.
- renaming a definition retains its previous label as an Excel alias.
- frontend table visibility remains a per-browser teacher preference; it does
  not change tenant data or the shared definition.

Changing this boundary requires tenant-isolation, rename/alias, deactivate/
reactivate, single-create/update, and Excel round-trip tests. Do not introduce
per-teacher physical database columns or repurpose core student columns.

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
- student creation and registration approval stage, but do not send, the initial
  account notice. The first confirmed active enrollment dispatches it once.
- staged student and parent password notice values are encrypted separately and
  removed only after all expected durable outbox rows exist.
- first-enrollment notices use service-returned parent password phrases:
  - new parent account: parent initial password phrase;
  - existing parent account: `변경되지 않음`.
- account notification logs are linked back through `source_tenant_id`,
  `target_type="account"`, and stable target IDs.
- student detail UI may show account-notification status metadata, never the
  message body or temporary password.
- preexisting students have no pending notice marker, so adding another lecture
  after this behavior ships does not back-send a historical welcome message.

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

### Staff student-support session and ended-lecture boundary

The staff support-preview and student activity contract is owned by
`student-support-audit.md`. Support tokens revalidate the operator's active
staff access on every request and never create a student login event. Exact
homework, video, and result opens are stored in the same `OpsAuditLog` activity
stream as screen-level evidence; records begin at feature release and are not
inferred retroactively.

An ended lecture (`Lecture.is_active=False`) is not current learning access even
when its `Enrollment.status` remains `ACTIVE`. Student session, exam, homework
submission target, video list, playback, and progress paths must require an
active lecture. Historical grades and video progress use the separate readonly
history selector and may include ended lectures; they must not return a playable
session or media URL.

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
python -m pytest apps\domains\students\tests\test_student_support.py -v --tb=short -x
```

### Student video playback controls

`FREE_REVIEW` and `PROCTORED_CLASS` decide whether monitored playback sessions
and event writes are required. They do not erase the teacher's saved video
controls. In review mode, `Video.allow_skip`, `Video.max_speed`, and
`Video.show_watermark` are returned as the effective seek, playback-rate, and
watermark policy. In proctored mode the stricter class controls remain in
force. The student player must consume the nested `policy` returned by
`GET /api/v1/student/video/videos/{video_id}/playback/`; the flat video fields
are display metadata, not a second policy source.

If playback policy cannot be resolved for the selected active enrollment, the
request fails closed rather than choosing another enrollment or tenant.

An `ACTIVE` enrollment alone does not keep a paid lecture open after the
lecture is ended. When a regular `Lecture.is_active` becomes false, student and
parent operational reads remove its sessions, current exams, video library,
playback and progress writes immediately. The tenant-wide `is_system` public
library remains available. Published exam and homework history stays readable
from the grades contract so ending a lecture revokes learning access without
erasing the student's record.

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
