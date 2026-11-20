# Student Domain Core SSOT

**Status:** Active
**Last checked:** 2026-09-20 KST
**Truth basis:** code inspection of `apps/domains/students/`, `apps/domains/enrollment/`, staff attendance projections, `apps/core/views/account_recovery.py`, `apps/core/services/password.py`, `apps/domains/results/services/submission_scope_guard.py`, `apps/domains/results/services/student_result_service.py`, and frontend shared student contracts.

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

Student state is not one boolean. `deleted_at`, tenant account access,
`is_managed`, and each `Enrollment.status` are independent axes. `is_managed`
only controls staff management classification and must never be described or
implemented as login suspension. Deletion/restore and enrollment state transfer
are owned by `student-lifecycle.md`.

Tenant-scoped student, lecture-enrollment, and session-enrollment lists use
student name ascending with stable ID tie-breakers unless the user explicitly
chooses another supported ordering. Search, deleted-state, lecture, and session
scope are applied before this global ordering and pagination. PostgreSQL uses
the explicit `C` collation for the student-name expression so the production
database locale cannot replace Unicode codepoint 가나다 order; SQLite keeps its
equivalent native binary ordering. The shared list rules are owned by
`data-list-ordering.md`.

## 1. Canonical Student Graph

### Lecture handoff memo

`Enrollment.lecture_memo` is a shared staff handoff note for exactly one
tenant/student/lecture, for example “use video for every session of this course”.
Every session roster of that lecture reads the same enrollment field; another
lecture has a different enrollment and note. It does not change attendance or
video entitlement automatically. There is no new session-specific or personal
private memo. Existing `Student.memo` (student-wide) and `Attendance.memo`
(session attendance) retain their data and behavior; neither is copied into the
new field. Existing student/parent profile responses already expose `Student.memo`.

Active tenant memberships with role `owner`, `admin`, `staff`, or `teacher` use
the existing `TenantResolvedAndStaff` boundary. Parent/student roles cannot read
or write this lecture memo, including their own enrollment. The student/parent
profile API does not project the new field. Staff enrollment, session-enrollment,
and attendance serializers expose `lecture_memo` and `lecture_memo_updated_at`
(the enrollment's `updated_at`). The two roster serializers also expose
read-only `student_memo`, converting a null existing student memo to an empty
string without changing stored data. No per-row student fetch is needed.

`PATCH /api/v1/enrollments/{id}/lecture-memo/` accepts exactly
`{"lecture_memo": "text"}` plus required `X-Expected-Updated-At` from the last
read. Text is at most 2,000 characters, retains whitespace, and an empty string
clears it. Success returns `{id, lecture_memo, lecture_memo_updated_at}`.
The endpoint locks the tenant-scoped enrollment before checking the timestamp;
missing/malformed timestamps or invalid bodies return 400, stale writes return
409 with `code: stale_resource` and `current_updated_at`, and a foreign/missing
enrollment returns 404 for an otherwise authorized caller. On conflict, retain
the draft and reload the current memo before deliberate retry; never retry
silently with a new timestamp. Frontend interactions are owned by the frontend
attendance module documentation.

Only this endpoint writes the field. Generic enrollment updates cannot write
memo fields or save a stale memo incidentally; memo updates do not run fee,
account-notification, or enrollment-status side effects. The migration adds an
empty column with a persistent database default so old runtime inserts remain
compatible during rolling deployment. Old runtimes do not know or overwrite the
new column. There is no data-copy migration or new identity graph.

Inactivation/reactivation and student soft-delete/restore preserve the memo.
An explicit hard deletion of its enrollment/student/lecture removes it with that
enrollment; a fresh enrollment after hard deletion starts empty. Automated
disposable-enrollment correction refuses a nonempty memo as authored data.
Tests: `apps/domains/enrollment/tests/test_lecture_memo.py` covers distinct admin
and staff actors, role/tenant denial, profile non-disclosure, shared session
projections, other-lecture isolation, old notes, clear/reload, stale-write
protection, lifecycle preservation, cleanup protection, and old-runtime inserts.

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
- When a student phone is supplied, it is the requested login ID and any collision
  fails explicitly. A generated ID is used only when both the explicit ID and
  student phone are absent; a collision never silently changes the saved ID.
- A student login display ID must also be unique across every active login
  identity in the tenant, including parent accounts. If a no-phone student's
  requested ID equals the parent phone, creation and profile changes reject the
  collision instead of silently assigning another ID. A generated student ID is
  used only when the caller leaves the ID empty. This prevents a shared
  initial password from matching both the student and parent account and
  making login ambiguous.
- `Student.ps_number` and the inventory copies that scope student files use the
  same 50-character storage boundary; a valid student identity must not fail
  when it is mirrored into inventory metadata.
- internal username mirrors `ps_number` through `user_internal_username(tenant, ps_number)`.
- Persisting a `ps_number` change locks the account and student rows and updates
  the internal username, inventory copies, and student row in one transaction.
  A save whose `update_fields` excludes `ps_number` must not mutate either
  identity mirror. The persisted `Student.user_id` selects the account lock
  before the student row lock; an in-memory attempt to relink the account or
  tenant fails closed. Username collisions roll back every identity copy.
- student phone is optional; parent phone is required on creation/import/signup.
- phone fields are normalized to numeric `010XXXXXXXX` 11-digit strings.
- Public JWT login NFKC-normalizes and trims the submitted identifier. It removes
  spaces, hyphens, dots, and parentheses only when the compact value is exactly an
  `010` 11-digit mobile identifier; punctuation in custom IDs remains significant.
- an exact student-phone/parent-phone match means the student has no phone. The
  Excel parser, JSON import, signup approval, single create, and profile write
  store that value only as `parent_phone`; `Student.phone` and `User.phone` are
  empty and `uses_identifier=True`. The parent account remains the sole owner
  of the shared recipient number.
- `User.phone` mirrors `Student.phone`; profile changes update or clear both so
  account and notification paths cannot retain a stale student recipient.
- malformed student phone is rejected. Do not silently convert it to identifier mode.
- if student phone exists, `omr_code` is the last 8 digits of student phone.
- if student phone is absent, `omr_code` is the last 8 digits of parent phone.
- no fake student phone is created only to satisfy downstream code.
- `uses_identifier=True` means the student has no student phone and is identified by the account/OMR identifier flow.
- when staff first records a distinct real student phone for an identifier-mode
  account, the same transaction updates `Student.phone`, `User.phone`,
  `omr_code`, `uses_identifier`, `Student.ps_number`, and the internal username.
  A phone-owned login ID also follows a later phone change; an explicitly
  custom login ID remains unchanged. Passwords are never reset by this update,
  and the existing SYSTEM_AUTO account notice reports 실제 로그인 가능한 비밀번호.

Current canonical entry points:

| Flow | Canonical path |
|---|---|
| admin single create | `StudentCreateSerializer` -> `create_student_account()` |
| JSON bulk create | `import_students_from_rows()` |
| Excel/worker import | `ExcelParsingService` -> `import_students_from_rows()` |
| lecture/enrollment Excel import | `resolve_student_import_row()` |
| signup approval | `approve_registration_request()` -> reuse exact active identity or `create_student_account(password_hash=...)` |
| admin/student profile write | `update_student_profile()`; student self-service cannot change `parent_phone`/Parent linkage |
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

Legacy active rows whose student phone exactly equals the parent phone are
audited and repaired with the dry-run-first command below. Execution requires
the complete current candidate ID set and a tenant/count confirmation, fails
closed for pending account notices or active account outboxes, and changes no
login ID, password, parent account, enrollment, or notification:

```powershell
python manage.py repair_shared_student_parent_phones --tenant <tenant-code>
python manage.py repair_shared_student_parent_phones --tenant <tenant-code> --student-ids <id,id> --execute --confirm <tenant-code>:<count>
```

Student and enrollment Excel uploads accept only the `.xlsx` extension. The
API validates a non-empty bounded upload, a supported browser MIME (including
Windows Hancom HCell's `application/haansoftxlsx`, empty MIME, and generic
`application/octet-stream`), the ZIP signature, `[Content_Types].xml`, and
`xl/workbook.xml` before R2 upload. MIME is client metadata and never replaces
the file-content checks. A renamed file, unrelated MIME, disguised ZIP, or
corrupt workbook fails closed, and the worker parser remains the final workbook
and row-validation boundary.

Excel contacts and JSON student/enrollment imports use the same conservative
`apps/shared/utils/excel_phone.py` normalization. Fullwidth ASCII digits and
punctuation, presentation dashes, numeric cells missing the leading zero, and
one explicit relation prefix or suffix (`부`, `모`, `아버지`, `어머니`, `아빠`,
`엄마`, `부친`, `모친`, `부모`, `학부모`, `보호자`, `학생`, `본인`,
`father`, `mother`, `parent`, `guardian`) are accepted. Parentheses/brackets
and a colon following a prefix are supported. `010-0000-0000(부)` therefore
becomes `01000000000`. Arbitrary text, two contacts in one cell, malformed
groups, decimal/scientific text, and additional digits fail the row; the parser
never guesses a recipient. Existing eight-digit student identifiers retain
their parser-only legacy behavior. Names and manually authored records are
not rewritten by normalization or duplicate detection.

The worker independently validates the 10 MiB compressed/100 MiB expanded,
2,000-member OOXML archive, rejects duplicate/encrypted entries and document
type declarations, and closes all streams. Student files permit 50 sheets,
10,000 physical rows and 256 columns per selected sheet, with headers in the
first 100 rows. Selection samples at most 150 rows per sheet (100 header rows
plus 50 data rows), then reads only
the selected sheet. Untrusted worksheet dimensions neither hide actual rows
nor allocate an artificial full worksheet. Oversized input fails before any
registration. Per-row student errors preserve source row numbers for corrected
retry; ambiguous worksheet selection still requires an explicit active sheet.

Existing-student lecture enrollment also reports `enrolled_rows`,
`excluded_rows`, `excluded_count`, and `total` alongside the previous counts.
Every source row in a successful batch is accounted for as enrolled or excluded
(`invalid_row`, `not_found`, `ambiguous`, `duplicate_in_file`). `enrolled_count`
includes already-enrolled students, so replay does not imply new accounts.
Mixed valid/invalid workbooks enroll the matched valid rows and merge parser
errors into the same excluded-row ledger before atomic job completion.
An entirely unmatched batch still fails without creating an empty session.
The browser retains only its tenant-scoped safe result projection, never
phones, account passwords, or raw workbooks. See [Excel operations](../operations/excel-operations.md)
for scope, recovery, and verification; frontend interactions remain owned by
the frontend documentation.

Student app profile photos use only the tenant-scoped R2 key returned by
`profile_photo_key(tenant_id, student_id, unique_id, ext)` as the readable
production state. An R2 upload or DB key-save failure returns retryable 503 and
must not fall back to a local `ImageField` that production cannot serve. After
the new key is saved, the exact replaced R2 object is deleted best-effort; a DB
save failure cleans the newly uploaded key best-effort and preserves the old DB
key. Invalid content type, image magic bytes, or files larger than 10 MiB are
rejected before upload.

The admin student list and detail responses sign each present profile photo once per serialized
student through `profile_photo_url`; the final representation reuses that field.
Missing photos still return `null`. Pagination, tenant scope, tags, enrollments,
account state and clinic highlights are unchanged. The list regression checks a
mixed page with and without photos and verifies one signing operation per photo.
Detail regression also covers a missing photo and signing failure, preserving the
existing `null` response without a second signing attempt.

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

- signup create requires write-only `initial_password` and
  `password_confirmation`. The serializer compares the exact untrimmed values
  before any request, Student, User, or membership write. A mismatch is a
  field-keyed 400 and creates nothing. Confirmation is not a model field and
  neither its plaintext nor a second hash is persisted or logged.
- a matching signup request stores the Django password hash and a tenant-bound
  encrypted password for the approved account notice; `initial_password_plain`
  remains empty. Approval/rejection clears the request ciphertext.
- signup approval keeps the exact chosen student password and stages that actual
  login value. A new parent independently uses the tenant parent rule. Legacy
  hash-only requests use a valid 30-day pending credential for the notice.
- signup approval status transition and student creation are atomic in
  `approve_registration_request()`.
- PostgreSQL approval acquires deterministic transaction locks for the
  same-tenant student phone, exact name+parent phone, and requested login ID
  before candidate lookup or account creation. Distinct pending request rows
  for one identity therefore cannot create parallel Student/Parent/User graphs.
- signup approval re-resolves the request against the current tenant before
  creating anything. One exact active student graph is reused in place: the
  request is linked and approved without changing the existing Student,
  Parent, User, membership, `ps_number`, password, `token_version`, or pending
  account-notice state. Active reuse sends no new account notice, so its internal approval result
  leaves the unused notice password fields empty.
- multiple active matches or a mismatched tenant/phone/account graph fails
  closed with 409. A deleted match is never restored automatically: the staff
  approval response returns only same-tenant candidates whose exact recovery
  identity matches, and staff must select one candidate through
  `POST /students/registration_requests/{id}/resolve_deleted/`.
- explicit deleted recovery locks Parent, related Users in ID order, Student,
  then memberships, rechecks the selected same-tenant deleted graph, and
  restores that exact Student/User. It applies the confirmed signup login ID
  and the already stored signup password hash, invalidates old tokens, and
  reactivates the existing student membership. An active collision, foreign
  tenant or membership, changed candidate, missing graph edge, or processed
  request returns 409 with no recovery write. Repeating a successful request
  also returns 409 and does not reapply password, token, profile, or history
  changes.
- recovery never clears or repoints an earlier registration audit link.
  `StudentRegistrationRequest.student` is a nullable non-unique ForeignKey with
  plural reverse relation `Student.registration_requests`; the expand-only
  migration preserves every existing link and permits the new recovery request
  to reference the same Student. Enrollment rows and links remain unchanged;
  their statuses are restored only by the canonical student lifecycle from the
  deletion snapshot. Original `ACTIVE`/`PENDING`/`INACTIVE` states return when
  the lecture still accepts restoration, ended or inactive lectures stay
  `INACTIVE`, and the internal marker is consumed and cleared. This recovery
  endpoint does not enqueue an account notice or message.
- active reuse also requires active Student and Parent
  login users with canonical internal usernames and phone mirrors plus active
  same-tenant `student` and `parent` memberships. A reused Student must already
  have a linked Parent User; only the new-student path may let the existing
  parent ensure contract repair a Parent whose `user_id` is empty. A phone match
  in another tenant is never reused or treated as a same-tenant identity.
- `godmin` and `tchul` do not use student self-registration. Public create and
  duplicate-check APIs and direct approval all fail closed with 403; existing
  login and account-recovery flows remain available.
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
    `temp_password`; account-notice opt-out parameters are not part of the
    contract and cannot suppress SYSTEM_AUTO account notices;
  - student target may resolve by `student_ps_number` or verified student phone;
  - parent target resolves by student name + parent phone;
  - password changes immediately;
  - pending reset is cleared;
  - Alimtalk delivery failure rolls back the password and pending-reset state.
- password minimum length remains 4. Do not raise it.

Focused verification:

```powershell
python -m pytest apps/domains/students/tests/test_registration_approval_identity.py -v
python -m pytest apps/domains/students/tests/test_registration_request_history_migration.py -v
$env:DJANGO_SETTINGS_MODULE='apps.api.config.settings.test_pg'
python -m pytest apps/domains/students/tests/test_registration_approval_concurrency_pg.py -v
```
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
  실제 로그인 가능한 비밀번호.
- student creation and registration approval stage, but do not send, the initial
  account notice. The first confirmed active enrollment dispatches it once.
- staged student and parent password notice values are encrypted separately and
  removed only after all expected durable outbox rows exist.
- first-enrollment notices use service-returned parent password phrases:
  - new parent account: parent initial password phrase;
  - existing parent account: 실제 로그인 가능한 비밀번호.
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
| Clinic | clinic target and remediation state must resolve through enrollment/session context; `/clinic/idcard/` returns every unresolved target across active enrollments in newest-first order, with the actual source title, nullable dedicated scope, session label, and a separate tenant-local reservation/passcard projection |
| Homework | assignment/submission rows must carry tenant-scoped enrollment identity |
| Attendance | attendance status that affects secession/enrollment must call the lifecycle path, not mutate student rows directly |
| Video/progress | student visibility and progress must use tenant-scoped enrollment/session access |
| QnA/community/counseling | student author/target must be tenant-scoped and not inferred from display name |
| Messaging | recipients come from the verified student/parent phone in the resolved student graph |

Clinic source projection never guesses from an uploaded filename or copies a free-form
description into scope. `source_title` is the tenant-scoped Exam/Homework title, and
`source_scope` stays `null` until that source domain owns a dedicated unit/range value;
clients render the missing scope explicitly instead of hiding the rest of the target.
The passcard keeps the unresolved `ClinicLink` verdict in `current_result` and returns
the same selected-student projection for a student and their authorized parent.
`GET /clinic/idcard/` uses the shared student-app request resolver: a student always
reads their own active profile; a parent must explicitly select an active linked
child with `X-Student-Id`. Switching children recomputes targets, bookings and the
verdict from that child's enrollments. Missing/invalid selection, another parent's
child, another tenant, deleted students, and non-student/non-parent callers fail
with `403`; an unresolved identity must never become an empty `SUCCESS` passcard.
The existing client error/retry state handles rejection, and selecting an authorized
child restores the ordinary passcard journey. No student, grade, enrollment or
clinic records are migrated or changed by this read-side correction. Regression
coverage in `StudentClinicPermissionAPITest` verifies repeated GETs, child switching,
two unresolved homework targets, booking confirmation, resolution and isolation.
An identified student with no active enrollment also receives `403`, with
`code=clinic_no_active_enrollment`, instead of an empty passing/departure credential.
This includes an account with no enrollment history and one whose enrollments are
all inactive, even when a preserved clinic booking exists. Older clients enter
their existing error state; the updated frontend explains the empty enrollment
state and offers a fresh check. Selecting an enrolled child or restoring the
ordinary enrollment restores the existing verdict on the next GET. The read does
not alter enrollments, grades, targets or reservations. Regression coverage checks
both roles, repeated reads, child selection and recovery with unresolved evidence
preserved; active students keep the existing PASS/FAIL/booking contract.
The response also returns
the separate `passcard_state` (`PASSED`, `CLINIC_REQUIRED`, or
`BOOKING_CONFIRMED`). A future-or-today `booked` reservation yields
`BOOKING_CONFIRMED` without resolving any `ClinicLink`. After check-in, `attended`
keeps that state across local-date changes until `completed_at` is recorded, but only
for assessments on or before the clinic's scheduled date. Every confirming or visible
booking must be on or after the latest live unresolved assessment session date across
the student's active enrollments. A session without a date uses the tenant-local date
of its `ClinicLink.created_at`; an undated source must not permit unlimited reuse of
old attendance. Same-date clinics remain supported because the assessment schedule
owns a date, not an exam-end timestamp. An older attended clinic (including one with
checkout recorded but no completion) cannot confirm a newer assessment. The API
selects the next qualifying booking instead; no attendance, booking, score, or
`ClinicLink` history is rewritten by this read projection. A future booking scheduled
before a future assessment is also insufficient. Resolved, removed, or completed
sources do not move the cutoff. Completion
ends the booking state immediately: if any link is still unresolved, the student
returns to `CLINIC_REQUIRED` until the next confirmed booking; if every link has been
resolved, the state is `PASSED`. `cancelled`, `rejected`, and `no_show` never confirm
the booking state. `pending` remains visible as `승인 대기` but stays
`CLINIC_REQUIRED`. `valid_bookings` is sorted by effective date, start time, status
priority, and participant id. For an unresolved student, `current_booking` is the first
confirmed or in-progress item when one exists; otherwise it is the first visible
pending item. The projection includes only status and schedule fields, not student or
parent PII. Manual clinic PDFs remain independently authored content and do not
inherit this projection.

`name_highlight_clinic_target` is the administrative projection of the same
student-level state, not an independent attendance flag. It is `true` only while
the student's passcard is `CLINIC_REQUIRED`. A confirmed future/today booking or an
incomplete `attended` clinic that meets the same assessment-date cutoff changes the
passcard to `BOOKING_CONFIRMED` and removes
the yellow name highlight from every unresolved enrollment for that student.
`pending` does not remove it. When clinic work receives `completed_at`, unresolved
links make both the passcard and yellow highlight return immediately; resolving all
links makes the passcard `PASSED` and keeps the highlight off. All projections use
tenant-scoped student and enrollment relationships and fail closed on missing data.

Regression entry: `apps.domains.clinic.tests.StudentClinicPermissionAPITest` exercises
old attendance, a future booking before the assessment, same-day positive booking,
reload consistency, and a later requirement in another active lecture. Both idcard
and `compute_clinic_highlight_map` must agree while source and attendance rows remain
unchanged.

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

### Parent-selected learning submissions

A parent may submit an online exam or homework media only for the active linked
student named by `X-Student-Id`. The server never guesses a child when that header
is absent and never falls back after an invalid, unlinked, deleted, or cross-tenant
student ID. Enrollment, active lecture, and exact exam/homework assignment checks
still run before any submission row or object-store write.

The resulting `Submission.user` is the selected student's User so grades, pending
work, teacher review, and reload projections remain identical to a student-authored
submission. When the authenticated actor is the parent, the submission metadata
records `submitted_by_user_id`.

The same exact selected-child rule applies to community question/counsel writes,
student inventory and reported-score evidence, and video progress. These writes
persist to the selected child's ordinary rows so the parent, student, and staff
reload projections agree. Reported scores preserve the parent as `submitted_by`;
community posts preserve `author_role=parent`. A parent video progress POST no
longer returns an unsaved echo. Missing, stale, sibling-selected, unlinked, deleted,
or cross-tenant child context fails before mutation. Profile, account identity,
password, and administrator-only settings remain outside delegated learning access.

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
python -m pytest tests\test_video_access_security.py -v --tb=short -x
python -m pytest apps\domains\students\tests\test_student_support.py -v --tb=short -x
```

### Student video playback controls

Tenant-resolved staff can change the default `allow_skip`, `max_speed`, and
`show_watermark` values through the ordinary video detail PATCH. An actual
default-policy change locks the video row and increments `Video.policy_version`
exactly once; title/order-only or no-op PATCH requests do not increment it. This
lets the student's periodic access check reject a stale playback grant while
avoiding needless interruption when the effective defaults did not change.

For one session, staff can update skip and/or speed defaults for selected videos
with `POST /api/v1/media/videos/bulk-policy/`. The JSON body contains the exact
positive `session_id`, one to 500 unique positive `video_ids`, and at least one
of `allow_skip` or `max_speed`. The action first locks the tenant-owned session
and all active requested video rows in deterministic order. Every ID must be an
active video in that exact tenant and session; a missing, deleted, duplicate,
cross-session, or cross-tenant ID rejects the whole request with no writes.
Only rows whose saved value changes are written, and each changed row increments
`policy_version` once. Student-level `VideoAccess` overrides continue to take
precedence over these video defaults.

`FREE_REVIEW` and `PROCTORED_CLASS` decide whether monitored playback sessions
and event writes are required. They do not erase the teacher's saved video
controls. In every non-blocked mode, `Video.allow_skip=True` (or a student-level
`allow_skip_override=True`) keeps free seeking even when an `ONLINE` attendance
still requires monitored playback. `FREE_REVIEW` also always permits free
seeking because the student is not completing a monitored substitute-attendance
watch. In review mode, `Video.max_speed` and `Video.show_watermark` remain the
effective playback-rate and watermark policy.

For a first `PROCTORED_CLASS` watch with skipping disabled, arbitrary forward
seeking is blocked, but the student may move forward through the server-approved
`budgeted_forward` control in fixed 10-second steps. The per-video allowance is
the smaller of 20% of the encoded duration and 30 minutes.
`VideoProgress.forward_skip_seconds_used` is the
server-owned cumulative counter, so refreshes, devices, tabs, and new playback
sessions do not reset it. `POST
/api/v1/student/video/videos/{video_id}/forward-skip/` locks the selected
enrollment before granting at most one step; the final grant may be shorter than
10 seconds and concurrent requests cannot exceed the allowance. Approved jumps
advance the ordinary playback position and therefore count as permitted course
progress. Backward 10-second movement remains available. Arbitrary progress-bar
forward movement and direct unapproved seeks still fail closed and enter the
proctored event stream.

The budget is unavailable when the duration is missing, and an explicit
`VideoAccess.block_seek` still blocks all seeking. Parents may watch through the
existing selected-child contract but cannot consume the student's persisted
skip allowance. Once completed `VideoProgress` or
`VideoAccess.proctored_completed_at` changes the video to `FREE_REVIEW`, free
seeking is restored; ordinary offline/review students receive the same free
seeking without needing a prior per-video completion row. No review-mode rule
overrides an explicit `block_seek=True`.
Completion also releases an explicit `PROCTORED_CLASS`/legacy `once` override:
the student's ordinary progress save is sufficient, and the completion marker
works without a progress row. `BLOCKED`, inactive enrollment authorization and
exact session membership checks still precede this completion rule.

An already admitted monitored playback session may finish across that completion
transition. With unchanged `policy_version`, current exact ACTIVE enrollment
access and completed progress or a completion marker, its existing
`PROCTORED_CLASS` token remains valid for refresh, heartbeat, renewal and final
event delivery. Renewal retains the signed mode, session identity and event
protocol; new playback bootstrap returns `FREE_REVIEW` without a monitored
session. Late events use the current effective policy, so free review seeking
does not become a violation, and protocol2 final receipts remain atomic and
idempotent. This exception never revives an expired/revoked session or survives
withdrawal, `BLOCKED`, a policy-version change or an unrelated access-mode change.
The player can therefore adopt review controls without interrupting playback.
The student player must consume the nested `policy` returned by
`POST /api/v1/student/video/videos/{video_id}/playback/`; the flat video fields
are display metadata, not a second policy source.

The cross-repository rollout is backend-first: the backend adds the explicit
POST bootstrap and makes a GET without `access_check=true` fail closed with
HTTP 405. The frontend then switches to POST against that sealed contract.
There is no compatibility fallback from POST to the legacy mutating GET;
read-only access checks remain GET throughout the rollout.

Tenant-wide public-library videos intentionally have no enrollment-specific
`access_mode`. Their effective playback policy is therefore resolved as
`FREE_REVIEW`; the response keeps the public video's flat `access_mode=null`
compatibility field while the nested policy remains complete and executable,
including free seeking without a per-enrollment skip budget.

For active enrollments, lightweight read-only
`GET .../playback/?access_check=true` uses the same effective
access-mode resolver as playback issuance. Explicit offline `PROCTORED_CLASS`
and online `FREE_REVIEW` overrides therefore return the exact mode and
monitoring flag that playback enforces, while `BLOCKED` remains a 403.

### Exact video access after enrollment deactivation

An inactive enrollment does not inherit video access from a fee, invoice,
payment, attendance row, or legacy `VideoAccess` override. When an operator has
separate authorization to preserve one already-earned video, the only exception
is an explicit `InactiveVideoEntitlement` for the same tenant, student,
enrollment, and video. The entitlement records its access mode, source and
source reference, nonblank reason, actor reference, grant time, optional expiry,
and revoke actor/reason/time. Revoked grants remain as history; a partial unique
constraint permits only one current grant for the exact enrollment and video.

The exception is valid only while the student, user, and student membership are
active; the enrollment is `INACTIVE`; the lecture remains active; the video is
`READY`; and the enrollment still has an exact `SessionEnrollment` for the
video's session. Tenant, student, enrollment, lecture, session, and video must
all agree. Expired or revoked grants fail closed. An explicit `BLOCKED` video
access row wins over the entitlement. Legacy `VideoAccess` rows never make an
inactive enrollment eligible on their own.
Soft-deleted videos (`deleted_at` set) are ineligible even when their retained
foreign-key row or R2 object still exists: student home/session surfaces omit
the video and thumbnail, playback/write resolution fails closed, and staff
history reports the entitlement as `INELIGIBLE`.

The target must be Academy-hosted revocable media. YouTube embeds cannot be
bounded or revoked by the Academy CDN contract, so staff grant returns HTTP 400
with `code=video_source_unsupported`, and a legacy/direct YouTube entitlement
row still fails closed at runtime.

Student video home and session-video responses expose only the exact entitled
session and video. They do not reopen future sessions, exams, homework,
attendance, fees, social actions, or the rest of the lecture. Playback,
required progress and forward-skip writes, and lightweight
`?access_check=true` revalidation use the same access context. Likes and
comments remain active-enrollment-only and return 403 for an inactive
enrollment even when that exact video is entitled. The access check returns
only `ok`, effective `access_mode`,
`monitoring_enabled`, and `policy_version`; it does not create playback
sessions, activity records, or view counts. A full `POST .../playback/`
bootstrap rechecks the
lecture, enrollment, video, and effective mode under row locks before recording
activity or view count. Every mode receives a bounded current-access token;
only `PROCTORED_CLASS` creates a monitored playback session. For ordinary
active playback, the current-access token and monitored session lease remain
bounded to `VIDEO_PLAYBACK_TTL_SECONDS`; the signed HLS URL is separately
bounded to the encoded video duration plus that grace period. Heartbeat extends
the monitored lease, while token renewal rotates the short current-access
credential. Keeping the media URL, token, and session lease separate prevents
the player source from being replaced every 10 minutes while preserving
revocation checks and 30-second access revalidation.

Before a current-access token expires, the client renews it through
`POST /api/v1/media/playback/renew/`. Renewal revalidates the exact current
tenant, user, student, enrollment or direct entitlement, lecture, session,
video, access mode, and policy version. It returns a new token and expiry while
preserving the existing monitored `VideoPlaybackSession`; it does not create a
new playback session or increment view/activity counters. Ordinary active
enrollment media keeps its existing duration-bounded HLS URL. Direct and
inactive-entitlement media receive a newly signed URL bounded by the renewed
short expiry. A revoked, expired, cross-scope, ended-lecture, changed-policy, or
inactive monitored session fails closed instead of being recreated by refresh.

For inactive entitlements only, the playback token, HLS URL, and thumbnail URL
expire at the earliest of the current access TTL (600 seconds by default) and
`expires_at`; configuration drift above 600 seconds is clamped back to 600 for
this inactive-only path. The locked playback grant is authoritative: HLS and
thumbnail signatures are rebuilt after that grant using its exact expiry. A
single absolute expiry from the locked clock is reused for the monitored session,
playback token, response, HLS URL, and thumbnail URL. A scheduler delay or a
concurrent replacement with a shorter entitlement cannot extend any one of
those artifacts. CDN workers
validate only the URL expiry and HMAC; they have no entitlement callback.
Therefore revoke immediately blocks new access checks, playback grants, and
token validation, while an already issued CDN URL can remain usable until its
bounded expiry (at most the access TTL). This contract does not claim immediate
revocation of an already issued CDN signature. For active, system/public, and
other ordinary playback, the HLS signature lasts at most the encoded video
duration plus `VIDEO_PLAYBACK_TTL_SECONDS`; the playback token remains
short-lived and renewable. Legacy READY rows without duration retain the
historical 24-hour media ceiling. This bounds already-issued ordinary media
after a lecture close without extending the monitored session lease or token
lifetime.
Signed URL query parameters and playback tokens are bearer credentials and are
never logged; playback logs retain only video id, safe status/path metadata,
and expiry timestamps.

Inactive-entitlement progress and forward-skip writes lock the exact lecture,
session, enrollment, video policy version, and entitlement in the same order,
then revalidate immediately before mutation. If revoke or another policy change
commits after the request's initial access check, the stale write returns 403
and creates or updates no progress or skip-budget row. Ordinary active
enrollment write behavior remains unchanged.

Staff manage this exception through tenant-resolved video administration:

- `GET /api/v1/media/inactive-video-entitlements/` lists auditable grants and
  accepts exact `student_id`, `enrollment_id`, and `video_id` filters;
- `POST /api/v1/media/inactive-video-entitlements/` grants or idempotently
  refreshes one exact current entitlement;
- `POST /api/v1/media/inactive-video-entitlements/{id}/revoke/` revokes it
  idempotently.

Grant and revoke lock the scoped rows and validate the current graph again
inside the transaction. They do not increment the video-wide
`Video.policy_version`: exact entitlement row state and expiry are revalidated
directly, so unrelated ACTIVE students' existing tokens and monitored sessions
remain valid.
An operator may stage a grant while the exact enrollment is still `ACTIVE` so a
withdrawal transition can be completed without an access gap; the staged row is
reported as `STAGED` and has no effect on ordinary active-enrollment policy. It
becomes eligible only after that same enrollment is `INACTIVE` and every other
inactive-entitlement guard passes.
The list reports `INELIGIBLE` instead of `ACTIVE` if the enrollment is inactive
but a current account, lecture, video, session-scope, tenant, or blocking guard
no longer passes.
They do not alter student, enrollment, attendance, session-enrollment, fee,
invoice, payment, exam, or homework state and do not enqueue notifications.
`STAFF_AUTHORIZATION` is an explicit manual source; it must not be presented as
proof that an invoice or payment exists. Both a database CHECK constraint and
runtime scope validation reject every other stored source, so a direct import
cannot turn a payment-derived or unknown value into learning access.

If playback policy cannot be resolved for the selected active enrollment or
the exact inactive entitlement, the request fails closed rather than choosing
another enrollment or tenant.

An `ACTIVE` enrollment alone does not keep a paid lecture open after the
lecture is ended. When a regular `Lecture.is_active` becomes false, student and
parent operational reads remove its sessions, current exams, video library,
playback and progress writes immediately. The tenant-wide `is_system` public
library remains available. Published exam and homework history stays readable
from the grades contract so ending a lecture revokes learning access without
erasing the student's record.

Regular lecture deactivation locks the lecture row and changes its state in the
same transaction that marks every ACTIVE monitored playback session for that
lecture `REVOKED`. A concurrent playback grant uses the same lecture-first lock,
so it either commits before close and is revoked by that close transaction, or
observes the committed close and issues no token/session. Refresh and heartbeat
reject existing regular-lecture tokens after close. Legacy protocol1 disposal may still
flush final Redis violation/total counters, but its ACTIVE-only status update
cannot downgrade a server `REVOKED` row to `ENDED`. System-library lectures are
excluded from this close-time revocation compatibility path.

### Receipt-aware playback audit and finalization (protocol2)

The normal student/selected-child parent `POST .../playback/` bootstrap, and
`POST /api/v1/media/playback/start/`, accept optional `event_protocol_version: 2`.
Omission remains protocol1. The grant response, signed playback token, and
monitored `VideoPlaybackSession.event_protocol_version` agree; renewal preserves
the protocol and session identity. FREE_REVIEW/direct access stays protocol1
without a monitored session. The existing GET access check remains read-only;
GET without `access_check=true` is still not a playback bootstrap.

Protocol1 retains its existing events/end and Redis write-behind semantics.
Existing sessions and historical counters are not upgraded, rewritten or merged.
Migration0023 adds a persistent database default1, not only a Python default:
old API processes inserting without the column continue to create protocol1.
A database CHECK allows only1/2. The new receipt table inherits exact tenant,
student, enrollment and video scope through its playback-session FK.

Protocol2 clients use distinct URLs so an old API cannot ignore the final event
payload and return a misleading200:

- `POST /api/v1/media/playback/v2/events/`: `{token, batch: {batch_id, events}}`.
- `POST /api/v1/media/playback/v2/end/`: `{token, batches: [...]}`; `batches` is
  required, with an explicit empty list when every prior event is acknowledged.

Each batch has a stable UUID,1-50 events and at most8KiB of normalized UTF-8 JSON.
Finalization accepts0-8 batches and at most200 events; the entire HTTP request,
including token and framing, must fit48KiB. Unknown envelope/event fields,
duplicate batch IDs, invalid JSON payloads, missing final batches and overflow
fail before mutation. A v2 session cannot use legacy events/end, and a v1
session cannot use the new URLs. This separation is intentional compatibility,
not an unavailable generally offered feature: clients select only the protocol
explicitly returned by their successful bootstrap.

`VideoPlaybackEventBatch` stores exact session+UUID uniqueness, a normalized
payload SHA256, event/violation counts and creation time. A token renewal or
server timestamp does not change the immutable batch identity. Same ID and
payload acknowledge the existing receipt without another write, including after
ENDED/REVOKED/EXPIRED; another payload under that ID conflicts. A previously
unknown batch after terminal status or expiry is rejected. All calls still
require valid signed token, authenticated user, resolved tenant and the exact
currently selected student/owned child; a duplicate is not a way to use another
tenant, child, session or payload.

The v2 service takes the existing lecture -> lesson -> enrollment -> video ->
optional inactive-entitlement scope locks before the exact playback-session
lock. It commits new receipts, audit rows, DB counters and final status in one
transaction. An admitted batch holds the session lock until its audit is durable,
so concurrent end cannot publish partial final counters. A final request carries
the same still-unacknowledged batches as in-flight ordinary requests: whichever
commits first stores the evidence, and the later request only acknowledges exact
receipts. This closes pre-admission overtaking without accepting arbitrary events
for an ended token. Current policy resolution must succeed; it cannot silently
substitute an empty policy. The existing violation threshold may make finalization
REVOKED, never falsely ENDED. Known receipts/empty disposal take only the exact
session lock and do not reopen closed access or acquire upper locks afterward.

For protocol2 the DB is also authoritative for the session lease, active state,
violation counts and revocation. Grant/heartbeat/renewal do not create or refresh
Redis session metadata, and events/end/revoke never import stale Redis counts.
Heartbeat updates only `last_seen`; only the existing permission-revalidated
renewal may extend `expires_at`. A heartbeat cannot outlive a short inactive
entitlement or shrink a newer lease when an older still-valid token arrives late.
Protocol2 generic lifecycle helpers identify the stored protocol before owner
filtering so a wrong student cannot fall through to a legacy Redis path.
Lecture-close and expiry remain status-only DB operations, preserving counters.
These changes do not alter video progress, attendance or learning-completion policy.

Successful events201/finalization200 return `protocol_version: 2`, current
`session_status`, `inserted_count` and `acknowledgements` containing each exact
`batch_id`, `event_count`, and `duplicate`. Inserted count is zero for a replay,
not the number acknowledged. Validation/scope/conflict responses are not success.
Unexpected policy/DB failures roll back the whole operation and return503 with
`detail=playback_events_unavailable` and a correlation `request_id`; server logs
retain the corresponding exception for investigation. Clients must keep immutable
pending batches until these exact acknowledgements, preserve them on retryable
failure, and show pending/retry rather than claiming a save. They must not drop
the old300-event queue prefix, clear non409 failures, or fall back to token-only
end for a protocol2 session.

The coordinated frontend HLS/YouTube controllers must drain oversized pending
queues during normal navigation and measure final request bytes, with at most
48KiB of owned simultaneous keepalive bodies per document. Auth generation,
tenant, selected-child and session isolation still apply; BFCache persisted
pagehide is not a final end. No offline credential store, unbounded unload batch,
arbitrary expired-token replay or guaranteed delivery after OS kill is provided.
Backend support alone is not proof that the currently deployed frontend uses it.
Frontend lifecycle ownership:
`academy-frontend/src/app_student/domains/video/playback/player/HEADLESS_REFACTOR.md`.

Migration0023 is explicitly classified as `contract`: the guard requires review
for its CHECK constraints and the non-null FK on the newly created receipt table.
Its compatibility is specific, not a guard exemption: existing/old-process session
inserts retain database default1, only1/2 are allowed, the receipt table starts
empty, and forward/reverse DDL remains atomic with bounded lock/statement timeouts.
PR validation uses `--allow-contract-review`; an ordinary automatic main push must
still refuse this migration. After rechecking these old/new API and database
conditions on the exact release main SHA, the release owner must use the official
`workflow_dispatch` with `allow_contract_migrations=true`, retaining all normal
development/preproduction/rolling health and continuity gates. Follow
[deployment modes](../operations/deployment-modes.md); metadata alone is not
permission to execute or proof of safe current runtime conditions.

Activation is reader-first: migrate and finish every serving BE instance before
deploying a client that opts into2. There is no global activation flag. An old
bootstrap response keeps that session on1; an accidentally old server receiving
a v2 URL returns an error, not a silent legacy success. Once2 sessions are issued,
BE rollback must retain v2 support until those sessions and renewable clients
drain, or use a compatible roll-forward. FE rollback alone does not stop already
open tabs; never force-reload them or assume a fixed TTL drains renewal.

Focused verification: `tests/test_video_final_event_atomicity_pg.py` uses actual
PostgreSQL connections and real DRF endpoints for both arrival orders, end lock
waiting, concurrent duplicates, rollback/retry, scope/parent isolation, terminal
state and DB-default compatibility. Redis-boundary mocks prove no-call/legacy
routing, not a live Redis deployment. Preserve existing
`tests/test_video_access_security.py`, inactive/direct-entitlement regressions,
and full official PostgreSQL CI. Completion additionally requires the coordinated
FE to perform actual fullscreen -> event acknowledgement -> finalization ->
DB/readback through reload/navigation on desktop and390px in isolated development,
followed by exact synthetic-data cleanup; APIRequestFactory is not HTTP-login E2E.

Add for frontend account/student UI changes:

```powershell
cd C:\academy\frontend
pnpm typecheck
pnpm guard:legacy-api
pnpm build
pnpm exec playwright test e2e\auth\account-recovery-modal.spec.ts --reporter=list
```

Current real-use work is tracked in [hardening-plan.md](../refactor/hardening-plan.md)
and follows the [release contract](../operations/change-risk-and-release-bundle.md).
`../refactor/student-domain-launch-readiness.md` is the historical 2026-06-07 decision.

## Staff student-detail question history (2026-10-09)

`GET /api/v1/community/posts/` accepts optional `author_student` as a positive
student ID. When present, it intersects the existing tenant, publication,
student/selected-parent-child visibility with `created_by_id` and the author's
tenant. Missing/foreign authors return an empty authorized result; empty,
malformed, non-positive and out-of-range filter values return 400 instead of
silently broadening the list. The student-detail UI combines this with
`post_type=qna`, so notices, counsel and other students' questions do not appear
as this student's questions. Counts and every page use the same filter, with
stable newest-first created-at/ID ordering. Calls without the filter retain
their existing behavior; no data is moved or rewritten and no migration is needed.

The frontend interaction owner is `academy-frontend/docs/ADMIN-STUDENT-DETAIL.md`.
Verification: `apps/domains/community/tests/test_student_question_history.py`
covers staff/parent/student visibility, malformed/foreign authors, inconsistent
cross-tenant references and the 50-row page boundary. Existing community
visibility tests remain required; frontend isolated real-use additionally proves
staff detail attribution after a parent creates a question for a selected child.

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

### Initial account password policy and notices (2026-10-01)

Staff explicitly choose student and parent methods in the final confirmation.
Legacy callers may use a supplied password or an explicitly saved role policy;
unset policies and unavailable phone suffixes fail without an implicit substitute.
Signup preserves the applicant-selected student password. A new Parent receives
the explicitly chosen method independently. The canonical policy is
[account-initial-password-policy.md](../ssot/account-initial-password-policy.md). Values in credential notices are verified against the active
hash or a valid pending login credential, including before first enrollment.
Legacy unknown hashes receive a reusable 30-day additional login credential;
existing passwords remain active until the delivered credential is used. No
placeholder prose is a password. The credential encryption, staff-only policy
API, failure/retry, parent sharing and verification contract is owned by
[parent-account.md](parent-account.md#초기-비밀번호-설정과-실제-로그인-안내-2026-10-01).
Existing rows/hashes are preserved by additive migrations; old API instances
can consume the new pending credentials during a compatible rolling release.

### 다원 검토 환경의 반 편성

`/api/v1/students/class-groups/`는 `DAWON_REVIEW_CLASS_GROUPS=True`인 독립 환경에서
다원 테넌트(719)의 인증된 교직원에게 제공한다. 학생·학부모는 접근할 수 없다. 반 이름은
같은 테넌트 안에서 유일하다. 연합반은 1%, HYPER, SKY 중 하나를 선택하며 학교별 내신반은
학교 이름을 지정한다. 학교명은 NFKC와 공백 정규화 후 일치해야 한다.

목록과 상세에서 명부를 확인하고, 반의 `candidates/`에서 이름 또는 소속 학교로 검색한 재원생(최대 50명)을
`PATCH members/`의 add/remove로 배정한다. 명부 갱신은 한 트랜잭션이며 다른 학원의 학생이나
추가 자격이 없는 학생이 섞이면 전체 요청이 거부된다. 학교 변경·삭제·관리 해제·사용자 또는 학생 멤버십 비활성으로 자격을 잃은 기존
구성원은 상세의 eligible=false로 확인하고 제외할 수 있다.

`POST lectures/`는 기간(vacation/midterm/final), 강의명, 시작일과 종료일을 받아 기존 강의와
재원생 수강을 한 트랜잭션으로 생성한다. 연합반 방학은 전체 명부, 연합반 중간·기말은 입력한 학교의 명부만 사용한다. 중간·기말에는 해당 학교의 재원생이 한 명 이상 있어야 한다. 학교별 내신반은 모든 기간에서 반에 지정한 학교를 사용한다. 강의 연결에 학교를 보존하고 이후 기존 강의의 수강
명부는 강의 관리에서 변경한다. 중복 강의명이나 자격이 변경된 구성원이 있으면 저장되지 않는다.
반을 만들거나 학생을 반에 배정하는 것만으로는 강의 수강·차시 출결이 생성되지 않는다.
상설반 상세와 기본 강의 목록은 연결된 강의에 `cycle_status`를 반환한다. 시작일 전은
`upcoming`, 시작일부터 종료일까지는 `current`, 종료일 다음 날은 `past`다. 같은 반에서
다음 방학이 시작되면 앞선 모든 기간이 지난 강의가 된다. 내신 기간이 시작되면 이전 방학과
해당 학교의 이전 내신 강의만 지난 강의가 된다. 같은 시작일은 나중에 만든 연결을 우선한다.
명부와 수강·성적·영상 권한은 보존한다. 같은 반·학교·기간 종류·시작일의 중복 생성은 거절한다.

검증은 `apps.domains.students.tests.test_class_groups`에서 권한, 명부 원자성, 실제 수강 기록 및
오류 복구를 확인한다.

강의 화면은 `GET for-lecture/{lecture_id}/`로 현재 테넌트 강의의 연결 반과 학교를 조회한다.
반 소속 강의에 뒤늦게 수강할 학생은 `{group_id}/lectures/{lecture_id}/late-students/`에서 조회하고
같은 주소에 `{"student_ids": [...]}`를 POST한다. 현재 반의 재원생, 해당 강의의 학교 조건,
기존 수강 이력 부재를 저장 시 다시 확인한다. 한 명이라도 조건을 벗어나면 전원 저장하지 않으며,
비활성 수강 이력은 보존된다. 반 후보와 추가 수강 후보는 offset으로 50명씩 조회할 수 있다.
엑셀에서 이름·연락처로 대조할 때 exact=true는 이름을 일치시키고 제공된 phone 및 parent_phone은
숫자만 비교한다. 강의 개설 시 담당자·과목·설명·시간·색상·칩 문구·기본 수업일은 기존 강의 검증을 따른다.

다원 반에서 개설한 강의의 시험 비교 기준은 기본 상위 30%이다. 원장·관리자와 강의에 연결된 담당
강사는 `for-lecture/{lecture_id}/`에서 기준을 조회하고 PATCH로 1~100을 저장한다. 기존 강의는
동명이인이 없는 재직 강사를 연결하며 담당자를 특정할 수 없으면 관리자만 수정한다.
시험 분석과 학생 본인의 시험 상세는 동일 시험·강의에 등록된 확정 최초 점수를 만점 대비 비율로 변환한다.
실제 0점은 포함하고 미응시·처리 중·미채점은 제외한다. 상위 인원은 올림하고 학생의 비교 비율은
본인 성취율을 상위 평균으로 나눈 백분율이다. 상위 평균이 0이거나 본인 점수가 미확정이면 비율은 null이다.

### 다원 선생님 학습 알림

전용 데모에서 원장·관리자는 지점 수업을, 강사는 자신의 담당 반 강의를 조회한다. 학생 알림함과 별도로 학습 알림은 출결, 시험, 과제, 대체수업 영상 기록을 읽는다. 강사는 항목별 사용 여부와 횟수·점수 변화·시청률 기준을 변경할 수 있다. 배정 영상 시청 부족은 지난 차시의 실제 대체수업 시청 의무와 준비된 영상이 있는 학생만 집계한다. 자유 복습, 접근 차단, 시청 완료는 제외한다. 학생 정보와 알림 조건은 다원 지점·담당 강사 범위에서만 제공하며 이 기능은 외부 메시지를 보내지 않는다.

### 다원 예시 수업 조회

공개 데모의 학생·학부모 화면은 `GET /api/v1/students/demo-learning/`에서 지정된 합성 학생의 수업을 읽는다. 서버는 `DAWON_REVIEW_CLASS_GROUPS`, 테넌트 ID 719와 코드 dawon, 관리 대상 예시 학생의 ID·이름을 모두 확인한다. 다른 대상을 선택하는 입력은 제공하지 않는다. 활성 수강과 해당 차시 등록, 개인 일정의 숨김 설정을 반영한다. 기간이 지난 강의도 기존 학생 조회 정책에서 허용하는 차시는 조회할 수 있다.

응답에는 강의와 차시의 제목·날짜·시각, 본인의 출결, 권한이 있는 준비 완료 영상의 제목, 학생 성적 조회 정책상 공개된 시험 결과, 배정된 과제와 제출 상태만 포함한다. 조회 실패 시 화면에서 다시 요청할 수 있다. 보호자의 요약 조회에는 학생 토큰을 발급하지 않는다. 실제 영상 진입에는 기존 학생 인증 및 영상 재생 권한 검사를 적용한다. 전용 검증은 `apps.domains.students.tests.test_dawon_demo_learning`을 사용한다.

다원 데모에서 반에 연결된 활성 강의의 공통 정규 차시를 개설하면 당시 강의의 활성 수강생을 차시 명단에 넣고 미입력 출결을 생성한다. 휴지통 학생과 비활성 수강생은 제외한다. 한 수업일로 개설한 차시는 명단을 그 날짜에 배정한다. 수업일이 여러 개면 교직원이 각 수강생을 날짜별로 배정한다. 차시와 명단, 출결, 최초 수업일 배정은 함께 저장하거나 함께 취소되며, 동일 요청 ID 재시도는 이미 생성된 차시를 반환한다. 별도 반·보강 차시, 기존 차시 명단은 종전 방식으로 관리한다. 변경은 새 차시 생성에만 적용한다.

### 다원 체험 관리자와 선생님

다원 검토 환경의 관리자 로그인은 다원 전용 관리 멤버십과 사용 불가능한 비밀번호를 가진 예시 계정을 확인한 뒤 두 시간의 체험 세션을 발급한다. 해당 세션은 본인 확인과 `/api/v1/students/demo-operations/`의 운영 현황 조회·직위 변경에만 사용한다. 반 명단과 연결 강의를 기준으로 학생·강의·차시 수를 집계하고, 반이 없는 강의는 미편성으로 표시한다. 시험별 채점 수와 평균은 해당 지점의 수강 강의와 연결된 정규 시험의 최초 응시 확정 점수로 계산한다. 미응시와 주관식 채점 대기는 제외하고 실제 0점은 포함한다. 직원 변경은 지정된 예시 직원의 강사·조교 표시 직위에 한한다. 대상이 일치하지 않으면 변경하지 않는다.

관리자 운영 조회는 같은 학원 반에 연결된 담당 교사의 이름(없으면 강의 제목과 다르고 학원 강사·교원 명단에서 확인한 기존 담당명), 차시명·날짜, 연결된 일반 과제명·진행 상태를 함께 제공한다. 과제별 활성 배정 수강생을 기준으로 제출·미제출·채점·검사 보류를 집계한다. 최초 점수(0점 포함) 또는 교사 승인도 제출·채점에 포함하고, 명시적 미제출과 영상 보강은 해당 상태를 우선한다. 여러 강의에서 같은 시험을 사용하면 강의별 통계는 해당 강의 수강 건의 최초 확정 점수로 각각 계산한다. 시험 탭의 전체 집계는 기존 범위를 유지한다.

선생님 체험은 기존 반·강의·차시·시험·성적·클리닉·상담 기능을 사용한다. 기존 강의·성적 API는 지점 단위이며, 강의별 비교 기준 변경과 강사별 학습 알림에는 반 강의 담당자 배정을 적용한다. 급여·직원 관리 접근은 체험 세션에서 제한한다. 역할·지점 경계와 직위 변경 후 재조회는 `apps.domains.students.tests.test_demo_operations`로 검증한다.

### 다원 공개 데모의 일자별 보기

전용 읽기 API는 격리된 예시 테넌트의 지정 학생에게 배정된 차시와 공개 기록을 반환한다. 학교 성적의 공개 요약은 기록일, 학년도·학기, 시험 구분으로 한정하며 점수 및 수정은 학생 인증 경계에 둔다. 공지는 공개 샘플로 지정한 게시물만, 클리닉은 전용 체험 일정과 동일 예시 학생의 참가 기록만 제공한다. 상담 목록에는 지정 예시 문구로 생성한 공유 학부모 신청의 날짜, 제목, 답변 상태만 포함한다. 부재·실패와 결과 미등록은 각각 표시하며, 클리닉·상담의 저장과 처리 권한은 각 도메인의 기존 API가 검증한다.

공개 달력, 일반 익명 조회, 체험 로그인은 클라이언트별로 서로 다른 카운터를 사용한다. 체험 로그인은 기존 익명 요청 설정(기본 분당 60회)을 그대로 따르며 네 역할이 같은 발급 한도를 공유한다. 로그인 한도를 소진하면 429 응답의 `Retry-After` 이후 다시 시도할 수 있다.

다원 선생님 대시보드는 `GET /api/v1/students/class-groups/teaching/`에서 현재 사용자가 강의 담당자로 연결된 다원 반, 해당 반의 수강 강의 ID와 가장 최근 차시를 조회한다. 기존 데모·테넌트·직원 접근 검사를 사용한다. 담당 반이 없으면 빈 목록을 반환한다. 현재 날짜에 수업이 없는 경우에도 지난 차시 기록에 접근할 수 있다.

날짜별 목록은 `GET /api/v1/lectures/sessions/?date=YYYY-MM-DD&class_group=<반 ID>`로 조회한다. 현재 교사의 담당 반에 속하는 같은 학원 강의만 반환한다. 최근 차시의 `date`와 `schedule_id`는 오늘까지의 실제 수업일을 가리킨다. 수업일을 따로 설정하지 않은 기존 차시는 기준일을 사용하고 `schedule_id`는 null이다.

선생님 체험에서는 차시 과제를 생성하고 이름·정책·문항 채점을 수정할 수 있다. 과제 화면에서 원본 시험 연결과 템플릿 저장도 지원한다. 데모 인증은 해당 생성·수정 경로와 메서드를 개별 확인하며 과제 API의 학원·역할·동시 수정 검증은 그대로 적용한다. 담당 반·실제 수업일과 데모 쓰기 범위는 `test_class_groups`와 `test_demo_operations`에서 검증한다.

### 다원 체험 학습 경보와 후속 일정

원장 전용 `students/demo-operations/`의 `alerts=1` 조회는 연결 반별 담당 강사의 기준을 적용한다. 최근 성적 하락은 직전 채점 3회의 평균 득점률에서 최신 시험 득점률을 뺀 값이다. 상위권 격차는 같은 강의·시험의 설정 상위 비율 평균과 개인 득점률의 차이다. 미채점·분모 없는 점수는 비교하지 않는다. 모든 기준과 교사 화면은 담당 강의 범위를 사용한다.

학생 상세는 활성 수강과 학생·강의 테넌트를 확인하며, 쓰기는 편성된 반의 학생·강의 관계도 검증한다. 원장 후속 일정 쓰기는 기존 전용 관리자 인증과 예약된 합성 학생 신원을 모두 요구한다. 상담은 수강·날짜 유일 예약으로 저장한다. 클리닉은 체험 전용 회차 표식과 동일 테넌트 수강을 확인하고 기존 참석자 등록 서비스를 사용한다. 재시도는 같은 예약을 유지한다. 학생·학부모 일정은 선택된 자녀 기준, 강사 일정은 담당 강의 기준으로 읽는다. 원장 상담 일정은 학부모가 작성한 상담 문의와 구분한다. 회귀 검증은 `test_dawon_followup` 및 `test_teacher_alerts`가 소유한다.

다원 데모의 반 목록과 명단·배정은 선생님용 로그인에서 담당 강의가 연결된 반 또는 직접 개설한 반으로 제한한다. 첫 강의 개설 전에도 만든 반을 다시 열어 학생을 배정할 수 있다. 기존 반은 담당 강의 연결을 기준으로 유지하고 관리자는 지점의 전체 반을 조회한다. 다른 선생님의 반을 직접 열거나 변경하면 404를 반환한다. 검증은 `test_teacher_directory_and_mutation_only_reach_owned_groups`가 담당한다.

### 다원 체험 관리자 반 편성과 담당 강사

전용 관리자 세션은 같은 테넌트의 예약된 활성 관리자 계정과 관리자 멤버십을 확인한다. 반 목록·상세·배정 후보 조회, 반 생성, 명단 배정·제외에만 기존 class-groups API를 사용한다. 반에서 학생을 제외해도 기존 수강·출결·성적은 보존한다. 상세 응답의 학생별 course_ids는 같은 반에 연결된 활성 강의와 같은 테넌트의 활성 수강이 모두 존재할 때만 반환한다.

원장은 class-groups/?view=instructors에서 같은 지점의 활성 강사 멤버십과 재직 강사 직위를 모두 충족하는 체험 직원의 사용자 ID·이름만 조회한다. 반 생성 시 선택한 assigned_teacher_id를 같은 조건으로 다시 확인하고 반의 assigned_teacher에 저장한다. 운영 시나리오용 합성 강사는 로그인 비활성 상태를 유지하며, 직원이 퇴직하거나 비강사 직위이면 지정할 수 없다. 생성자(created_by)는 실제 원장으로 유지한다. 강사의 기존 강의 배정과 추가로 보이는 담당 반을 구분한다. 지정한 강사가 비활성 또는 미구성이라면 등록은 실패하고 입력을 수정해 재시도할 수 있다. 기존 반은 assigned_teacher가 비어 있으며 기존 강의 배정으로 계속 조회된다. 검증은 test_class_groups의 관리자 반 생성·배정·강의 조회 범위 및 세션 경로 제한 테스트에서 수행한다.

다원 체험 지점은 담당 반이 있는 합성 강사 12명을 제공한다. 운영자가 체험 직원의 직위를 바꾸어도 재시드할 때 그 선택을 보존한다. 반 생성 창에서는 현재 강사 직위와 강사 멤버십을 모두 가진 직원만 선택할 수 있다.


다원 반 담당자 변경은 같은 테넌트 원장이 검증된 재직 강사의 사용자 ID를 반 상세 PATCH에 보낼 때 처리한다. 같은 트랜잭션에서 반 담당자와 현재·예정 강의의 담당 연결 및 강사 표시명을 갱신한다. 지난 강의 담당자와 기존 성적·수강 기록은 유지한다. 신규 수업은 반 담당자에게 연결되며 원장은 담당 미지정 상태에서는 강의를 개설할 수 없다. 강사의 반 범위는 명시된 반 담당자가 우선이고 지정 전 기존 반만 생성자·기존 강의 담당 관계를 사용한다.

원장과 담당 강사는 반 수업 개설과 늦은 수강 등록에 같은 수강 서비스를 사용한다. 대상 학생은 그 반 명단, 해당 테넌트, 재원·학교 조건에 맞아야 하며 기존 수강은 중복 생성하지 않는다. 반 생성·담당자 지정·반 인원 편성은 관리자 업무이다. 강사는 반 명단 읽기와 담당 반의 개별 수업 수강 등록을 할 수 있다.


R25 seed_dawon_staff는 전용 공개 강사 계정의 사번·신원 검증 후 활성 teacher 멤버십과 기존 담당 반 또는 활성 강의 담당 연결을 확인한다. 그 계정의 조교 직위만 강사로 정정해 실제 담당 강사가 반 배정 선택지에 포함되도록 한다. 다른 직원 직위는 보존한다.
