# Domain Boundaries

**Status:** [VERIFIED] current ownership and dependency audit with [PROPOSED] interfaces  
**Captured:** 2026-05-22  
**Goal:** make feature entrypoints converge by domain responsibility, not by folder cosmetics.

Boundary rule for the structure reform:

- Models remain where migrations and Django app labels currently require them.
- Public read access goes through selectors.
- Public writes go through services/use-cases.
- Cross-domain side effects go through events/tasks or explicit domain hooks.
- Tenant must be passed explicitly for every tenant-scoped read/write. No
  fallback tenant is introduced.

## Students

| Item | Current state |
|---|---|
| Owned data | `Student`, `Tag`, `StudentRegistrationRequest`; identity/profile/lifecycle fields; student schedule visibility fields |
| External references | Enrollment, attendance, clinic, exams/results/submissions, homework, messaging, fees, video, community, inventory, auth/core |
| Public interface candidate | `students.selectors` for tenant-scoped reads; `students.services` for create/update/lifecycle/profile/import/registration approval |
| Forbidden dependency | Other domains importing `Student` internals for write logic; model save touching inventory; frontend role apps importing admin student internals |
| Tenant rule | Every student lookup must include tenant and deleted-state intent; no global user/student lookup unless explicitly platform-admin scoped |
| Current risk | 6+ write roots, profile DTO drift, raw permanent delete, no-tenant repository helpers |

## Parents

| Item | Current state |
|---|---|
| Owned data | `Parent`, parent `User`, parent membership linkage |
| External references | Students, core auth, account recovery, messaging |
| Public interface candidate | `parents.services.ensure_parent_for_student`, `parents.selectors.get_parent_children(tenant, parent)` |
| Forbidden dependency | Student views deciding parent password/message semantics independently |
| Tenant rule | Parent phone lookup must include tenant |
| Current risk | Parent initial password meaning differs between parent service and welcome/registration messaging |

## Lectures / Classes / Sessions

| Item | Current state |
|---|---|
| Owned data | `Lecture`, `Section`, `SectionAssignment`, `Session` |
| External references | Enrollment, attendance, exams, homework, clinic, video, community |
| Public interface candidate | `lectures.selectors.get_session_for_tenant`, `lectures.services.manage_session` |
| Forbidden dependency | Other domains mutating lecture/session state without lecture service |
| Tenant rule | Lecture/session access must be tenant-scoped through lecture or session tenant path |
| Current risk | `/lectures/` prefix includes both lecture and attendance routes; session state participates in many domains |

## Enrollment

| Item | Current state |
|---|---|
| Owned data | `Enrollment`, `SessionEnrollment` |
| External references | Attendance, exams, homework, results, submissions, fees, clinic, video |
| Public interface candidate | `enrollment.selectors.get_roster`, `enrollment.services.enroll_student`, `enrollment.services.set_status` |
| Forbidden dependency | Students domain view directly owning enrollment-matrix toggles without enrollment service |
| Tenant rule | Enrollment and session enrollment queries must include tenant |
| Current risk | Student lifecycle changes also mutate enrollment status in multiple places |

## Attendance

| Item | Current state |
|---|---|
| Owned data | `Attendance` |
| External references | Enrollment/session, students for roster validation, messaging for notifications |
| Public interface candidate | `attendance.services.record_attendance`, `attendance.events.AttendanceChanged` |
| Forbidden dependency | Direct student lookup/write logic in attendance views beyond selector DTOs |
| Tenant rule | Attendance is tenant-scoped and should validate enrollment/session belong to same tenant |
| Current risk | Roster creation directly imports/queries Student; attendance state can trigger side effects |

## Clinic

| Item | Current state |
|---|---|
| Owned data | Clinic `Session`, `SessionParticipant`, `Test`, clinic `Submission` |
| External references | Student, enrollment, lectures, messaging |
| Public interface candidate | `clinic.services.add_participant`, `clinic.services.change_booking`, `clinic.events.ClinicStatusChanged` |
| Forbidden dependency | Serializer-owned student querysets/business decisions; students delete flow directly mutating clinic participants |
| Tenant rule | Participant/session/submission/test queries must include tenant and same-tenant student/enrollment |
| Current risk | Clinic has a `Submission` model name that conflicts conceptually with assessment submissions; participant status transitions live in views |

## Exams

| Item | Current state |
|---|---|
| Owned data | `Exam`, `ExamEnrollment`, sheets, questions, answer keys, template bundles, exam assets |
| External references | Lectures/sessions, enrollment, submissions, results, AI/OMR |
| Public interface candidate | `exams.selectors.get_exam_contract`, `exams.services.manage_exam_enrollment` |
| Forbidden dependency | Results/submissions mutating exam-owned enrollment or asset state directly |
| Tenant rule | Exam access must include direct `tenant` or verified session/lecture tenant path |
| Current risk | Exam creation/update/recalculation and OMR-related flows are spread across many API views |

## Homework

| Item | Current state |
|---|---|
| Owned data | Homework policy, homework enrollment, homework assignment |
| External references | Lectures/sessions, enrollment, submissions, homework_results/results |
| Public interface candidate | `homework.services.assign_homework`, `homework.selectors.get_assignments` |
| Forbidden dependency | Importing homework_results viewset through `homework.views.__init__` as if same owner |
| Tenant rule | Homework policy/assignment/enrollment must include tenant and validated enrollment |
| Current risk | `homework` and `homeworks` prefixes split ownership; score viewset belongs to `homework_results` but is exported by `homework` |

## Submissions

| Item | Current state |
|---|---|
| Owned data | Assessment `Submission`, `SubmissionAnswer` |
| External references | Exams, homework, enrollment, results, AI/OMR |
| Public interface candidate | `submissions.services.submit`, `submissions.services.discard`, `submissions.events.SubmissionChanged` |
| Forbidden dependency | Results/exams directly editing submission internals outside service hooks |
| Tenant rule | Submission and answers must include tenant; target exam/homework must resolve under same tenant |
| Current risk | Multiple OMR/admin/manual edit paths; result recomputation side effects need explicit events |

## Results

| Item | Current state |
|---|---|
| Owned data | `Result`, `ExamResult`, `ExamAttempt`, `ResultItem`, `ResultFact`, score edit draft, wrong-note PDFs |
| External references | Submissions, exams, enrollment/students, lectures/sessions |
| Public interface candidate | `results.selectors.get_student_grades`, `results.services.recalculate_attempt`, `results.events.ResultPublished` |
| Forbidden dependency | Student/teacher/admin views calculating grade state independently |
| Tenant rule | Results are tenant-scoped through enrollment/exam/session and must prove same tenant |
| Current risk | Same grade/status values are computed across result views, serializers, and frontend state |

## Messaging

| Item | Current state |
|---|---|
| Owned data | Notification logs, preview tokens, templates, auto-send config, scheduled notifications |
| External references | Students, parents, staff, attendance, clinic, exams/results, community |
| Public interface candidate | `messaging.services.enqueue_notification`, `messaging.services.resolve_recipients`, event handlers |
| Forbidden dependency | Domain views sending directly in the middle of transaction without event/outbox contract |
| Tenant rule | Every message/template/log must include tenant; recipients resolved from tenant-scoped selector |
| Current risk | Direct sends and rollback logic are repeated in student/password flows; source/use-case logging is inconsistent |

## AI / Matchup / OMR

| Item | Current state |
|---|---|
| Owned data | AI jobs/results/config/usage; matchup documents/problems/hit reports/page state/layout fingerprints |
| External references | Inventory/files, exams/submissions, landing, tenants |
| Public interface candidate | `ai.services.enqueue_job`, `matchup.services.create_document_job`, `omr.services.submit_batch` |
| Forbidden dependency | Domain views reaching worker/infra gateways directly without job contract |
| Tenant rule | Jobs and documents must include tenant or explicit system/platform scope; no tenant fallback for worker payloads |
| Current risk | Student Excel import dispatches AI jobs from student view; matchup has many tenant query paths and some public/iframe tenant resolution exceptions |

## Billing / Fees

| Item | Current state |
|---|---|
| Owned data | Billing surfaces plus fees `FeeTemplate`, `StudentFee`, `StudentInvoice`, `InvoiceItem`, `FeePayment` |
| External references | Students, enrollment, student app fees |
| Public interface candidate | `fees.services.assign_fee`, `fees.selectors.get_student_invoices` |
| Forbidden dependency | Student lifecycle deleting fee data directly without fees hook |
| Tenant rule | Fee/invoice/payment queries must include tenant and same-tenant student/enrollment |
| Current risk | Fee models direct-FK Student; permanent delete must coordinate before any destructive cleanup |

## Community

| Item | Current state |
|---|---|
| Owned data | Scope nodes, posts, mappings, replies, templates, likes, reports, notifications, attachments, blocks |
| External references | Students/users, lectures/sessions, messaging |
| Public interface candidate | `community.services.create_post`, `community.services.anonymize_or_unlink_author`, `community.events.ReplyCreated` |
| Forbidden dependency | Students permanent delete updating community internals from raw SQL |
| Tenant rule | Community content and moderation state must include tenant; author lookup must not cross tenant |
| Current risk | Created-by/user/student relations are repaired by commands and destructive student cleanup |

## Landing

| Item | Current state |
|---|---|
| Owned data | Public landing, consult requests, testimonials, tenant/program branding surfaces |
| External references | Tenant/core, community/matchup public preview in some flows |
| Public interface candidate | `landing.selectors.get_public_landing`, `landing.services.submit_consult` |
| Forbidden dependency | Product domains depending on landing internals for tenant resolution or public state |
| Tenant rule | Public forms still resolve a concrete tenant; no fallback tenant |
| Current risk | Public unauthenticated flows require strict tenant resolver and rate-limit contracts |

## Dependency Policy For Phase 1

- Allowed from other domains to students: public selector DTOs and service
  commands only.
- Forbidden from other domains to students: direct mutation of `Student`, direct
  reliance on `Student.save()` side effects, direct deleted-state filtering.
- Allowed from students to other domains: explicit domain hooks/events for
  enrollment deactivation, clinic participant cancellation, messaging
  notification, inventory identity rename.
- Forbidden from students to other domains: raw table surgery in views and model
  save imports.
- Allowed in frontend: role apps may use shared generated contracts.
- Forbidden in frontend: `auth`, `app_teacher`, `app_student`, `shared`, or
  `community/storage/clinic` importing `@admin/domains/students/*` internals.
