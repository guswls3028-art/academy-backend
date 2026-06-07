# Student Domain Phase 2 Stability Audit

**Status:** Active audit ledger
**Captured:** 2026-06-07 KST
**Owner SSOT:** `../domain/student-core.md`
**Launch gate:** `student-domain-launch-readiness.md`

This ledger turns the student-domain concern into executable invariants and
real-use scenarios. It is not a release note. It is the working checklist for
the next broad promotion decision.

## 1. Working Assumption

The student domain is the product spine. A screen that looks healthy is not
enough. The safe state is:

```text
tenant -> active account graph -> active student -> active enrollment/roster
       -> domain write -> student/parent projection -> notification/log evidence
```

When this chain is unavailable, the correct behavior is fail-closed with a clear
message, not fallback to display name, inactive enrollment, deleted student, or
another tenant.

## 2. Canonical Runtime Invariants

| Invariant | Canonical implementation | Required proof |
|---|---|---|
| Student account graph | `students/services/creation.py::create_student_account()` | User, Student, TenantMembership, Parent link are created or reused atomically |
| Signup approval graph | `students/services/registration_approval.py::approve_registration_request()` | Signup password remains hashed; approval creates student with requested login ID |
| Public recovery | `students/services/account_recovery.py` + `core/views/account_recovery.py` | Generic response; pending reset only activates on temporary-password login |
| Staff reset | `students/views/password_views.py` | Active staff only; rollback when notification delivery fails unless skip-notify is allowed |
| Active learning access | `enrollment/selectors.py::active_enrollments_for_student()` / `active_enrollments_for_students()` | Student-facing projections ignore inactive/deleted/cross-tenant enrollments |
| Result writes | `results/services/submission_scope_guard.py` | Exam, submission, enrollment, student, and tenant match before grading |
| OMR matching | `support/omr` candidate matching | Same-tenant active roster; ambiguous match stays manual |
| Account Alimtalk | `messaging/services/registration_service.py`, `account_recovery.py` | Exact approved owner templates; no SMS fallback; logs redact secrets |
| Frontend contracts | `frontend/src/shared/api/contracts/students.ts` | Admin/teacher imports use shared contract, not local shape guesses |

## 3. Abnormal Behavior Matrix

| User/system behavior | Expected result | Current evidence | Status |
|---|---|---|---|
| Student signs up with duplicate phone | Public response does not enumerate account details; duplicate path can trigger existing-credential recovery | Backend duplicate tests, UI duplicate modal test | Covered |
| Student signs up with malformed phone | Request rejected; no identifier-mode fallback | `test_registration_password_safety.py` | Covered |
| Signup approval notification fails | Approval remains committed; failure is logged, not hidden | `registration_views._send_registration_approved_notice()` inspection | Covered by code, needs operational log proof |
| Signup approval is double-clicked/raced | Row lock allows one approval; second request returns already processed | `approve_registration_request()` row lock tests | Covered |
| Public password recovery for unknown user | Same generic success message as known user | `test_account_recovery.py`, frontend modal spec | Covered |
| Public password recovery before temp login | Old password still works until temp password is used | Backend tests; frontend activation E2E still pending | Partial |
| Staff reset with stale/inactive membership | Request denied; no skip-notify bypass | Backend reset safety tests | Covered |
| Student has inactive enrollment with old score | Student app detail/summary must hide it | `test_grades_summary_homework.py` | Covered in this pass |
| Student reuses old `enrollment_id` for wrong note/PDF | Student role gets 403 when enrollment is inactive; staff historical access remains unchanged | `test_security_regression.py` | Covered in this pass |
| Student polls attempt history for inactive exam enrollment | Student role receives an empty list; query is scoped by active enrollment + ExamEnrollment | `test_security_regression.py` | Covered in this pass |
| Student hides a session from inactive enrollment | Request returns 404 and does not mutate hidden IDs | `test_session_tenant_isolation.py` | Covered in this pass |
| Student views attendance after enrollment inactivation | Inactive enrollment attendance is excluded from summary and recent rows | `test_session_tenant_isolation.py` | Covered in this pass |
| Student opens exam list/detail/submission after enrollment inactivation | Exam access resolves through canonical active enrollment ids | `test_parent_exam_child_selection.py`, selector inspection | Covered in this pass |
| Student opens a non-enrolled/non-accessible exam detail URL directly | Student app fails closed instead of staying in a retry/loading state | `frontend/e2e/flows/exam-data-flow.spec.ts` production bundle + production API | Covered in this pass |
| Student opens video list/stats after enrollment inactivation | Course video projection/progress excludes inactive enrollments; public system video is included only through explicit `include_system=True` | `test_student_video_progress_enrollment_resolution.py` | Covered in this pass |
| Dashboard scoped notice checks stale lecture enrollment | Notice scope uses canonical active enrollment lecture ids | `StudentDashboardView` inspection | Covered by code, needs dedicated notice fixture test if notice scope changes |
| OMR scan matches same name in another tenant | No cross-tenant match | `test_candidate_matching.py`, submission scope tests | Covered |
| OMR scan matches ambiguous same-tenant candidates | Manual review, not silent choice | OMR candidate tests | Covered |
| Student refreshes result before grading completes | Result endpoint fails closed or shows explicit pending state only | Needs final/draft policy decision | Open P1 |
| Parent has multiple children | Child switcher must scope all projections to selected active child | Existing parent switcher tests, needs chain inclusion | Partial |
| Student opens grades after withdrawal | Active enrollment selector hides scoped content | Backend test added for grades/exam detail | Covered |
| Student uses back/forward after logout | Auth state should not leak protected screen | Existing stability specs | Partial |
| User double-clicks signup submit | One pending request; duplicate/rate limit prevents repeat spam | Needs specific UI/API assertion | Open |
| Production signup canary uses arbitrary phone | Must be blocked; only `01031217466` is allowed | New Playwright guard | Covered |
| Controlled phone already belongs to active fixture | Do not run production signup canary; clean fixture or allocate dedicated controlled test recipient first | Read-only check found active fixtures `1890`, `1201` | Blocked |

## 4. Real-Use Scenario Ledger

| Scenario | Minimum complete path | Current proof | Gap |
|---|---|---|---|
| Public signup | Login page signup UI -> staff approval UI -> student UI login -> cleanup | `frontend/e2e/flows/signup-approval-roundtrip.spec.ts` implemented | Production execution blocked by controlled-number collision |
| Admin direct student create | Admin create -> student login -> profile/dashboard -> cleanup | Existing E2E fragments | Needs promoted canary |
| Account recovery | Public modal -> pending reset -> old password proof -> temp login activation -> must-change gate | Backend + modal tests | Needs browser activation spec |
| Teacher reset | Staff reset student/parent -> login proof -> restore | `password-reset-roundtrip.spec.ts` | Covered |
| OMR/result | Roster/exam -> submit/grade -> student result detail/grades/wrong-note/attempt history | `score-report-realuse.spec.ts`, backend scope/security tests | Promote into launch gate bundle |
| Clinic remediation | Failed result -> clinic target -> booking/approval/attendance -> student notification/projection | Backend/frontend fragments | Needs end-to-end canary |
| Homework | Homework assignment -> student submit -> admin grade -> student grades | API/render fragments; `homework-scores-inventory-data-flow.spec.ts` passed production bundle + production API | Needs full create-submit-grade canary |
| QnA/counsel | Student writes -> staff replies -> student sees reply | `qna-roundtrip`, `counsel-roundtrip` | Covered enough for current gate |
| Video/progress | Ready video -> student play -> progress persists -> resume | `test_student_video_progress_enrollment_resolution.py`; projection now uses canonical selector; `video-session-data-flow.spec.ts` production render/API passed | Needs playback/progress browser canary |
| Tenant isolation | Cross-tenant student/enrollment/result access denied | Backend tests + frontend tenant isolation gate | Covered |

## 5. Evidence Commands From This Pass

Backend already run:

```powershell
python -m pytest apps\domains\students\tests\test_student_identity_convergence.py apps\domains\students\tests\test_registration_password_safety.py apps\domains\students\tests\test_password_reset_safety.py apps\domains\students\tests\test_account_recovery.py -v --tb=short -x
python -m pytest apps\domains\students\tests\test_student_domain_stabilization.py apps\domains\results\tests\test_submission_scope_guard.py apps\support\omr\tests\test_candidate_matching.py -v --tb=short -x
python -m pytest apps\domains\student_app\tests\test_grades_summary_homework.py apps\domains\student_app\tests\test_parent_exam_child_selection.py apps\domains\student_app\tests\test_session_tenant_isolation.py -v --tb=short -x
python -m pytest apps\domains\student_app\tests\test_session_tenant_isolation.py apps\domains\results\tests\test_security_regression.py -v --tb=short -x
python -m pytest tests\test_student_video_progress_enrollment_resolution.py apps\domains\student_app\tests\test_parent_exam_child_selection.py apps\domains\student_app\tests\test_grades_summary_homework.py apps\domains\student_app\tests\test_session_tenant_isolation.py apps\domains\results\tests\test_security_regression.py -v --tb=short -x
python -m pytest apps\domains\results\tests\test_submission_scope_guard.py apps\support\omr\tests\test_candidate_matching.py -v --tb=short -x
python -m pytest apps\domains\student_app\tests\test_grades_summary_homework.py apps\domains\student_app\tests\test_parent_exam_child_selection.py apps\domains\student_app\tests\test_session_tenant_isolation.py apps\domains\results\tests\test_security_regression.py apps\domains\results\tests\test_submission_scope_guard.py apps\support\omr\tests\test_candidate_matching.py -v --tb=short -x
python -m ruff check apps/domains/enrollment/selectors.py apps/domains/student_app/exams/views.py apps/domains/student_app/media/views.py apps/domains/student_app/dashboard/views.py academy/application/use_cases/student_video_access_context.py tests/test_student_video_progress_enrollment_resolution.py
python -m ruff check apps\domains\enrollment\selectors.py apps\domains\results\services\student_result_service.py apps\domains\results\views\student_exam_attempts_view.py apps\domains\results\views\wrong_note_view.py apps\domains\results\views\wrong_note_pdf_view.py apps\domains\results\views\wrong_note_pdf_status_view.py apps\domains\student_app\results\views.py apps\domains\student_app\sessions\views.py apps\domains\student_app\tests\test_grades_summary_homework.py apps\domains\student_app\tests\test_session_tenant_isolation.py apps\domains\results\tests\test_security_regression.py
python -m ruff check apps/ academy/
python manage.py check --settings apps.api.config.settings.test
python manage.py makemigrations --check --dry-run --settings apps.api.config.settings.test
python -m pytest tests/test_smoke.py -v --tb=short -x
```

Frontend already run:

```powershell
pnpm exec eslint e2e\flows\signup-approval-roundtrip.spec.ts
pnpm exec playwright test e2e\auth\account-recovery-modal.spec.ts --reporter=list
pnpm exec playwright test e2e/flows/password-reset-roundtrip.spec.ts --reporter=list
pnpm typecheck
pnpm guard:legacy-api
pnpm lint
pnpm build
pnpm test:e2e:gate
pnpm exec playwright test e2e/student/score-report-realuse.spec.ts e2e/admin/session-assessment-realuse.spec.ts --reporter=list
pnpm exec playwright test e2e/flows/counsel-roundtrip.spec.ts e2e/flows/exam-data-flow.spec.ts e2e/flows/homework-scores-inventory-data-flow.spec.ts e2e/flows/video-session-data-flow.spec.ts --reporter=list
```

Additional frontend evidence from the production bundle against the production
API:

```text
pnpm test:e2e:gate -> 35 passed
score-report-realuse + session-assessment-realuse -> 2 passed
counsel + exam + homework/scores/inventory + video/session bundle -> 37 passed, 2 skipped
final counsel retry-cleanup hardening check -> counsel-roundtrip 5 passed
cleanup probe -> E2E Test Exam 0, [E2E] 상담 신청 0
```

The two skipped cases are conditional student exam submit/result steps when the
current production fixture has no accessible exam questions/results for the E2E
student. This is not counted as proof of a full exam-taking chain.

Deployment evidence:

```text
backend GitHub Actions run 27077258150 -> success; all six V1 images built/pushed and deploy verification passed
backend post-deploy local verification -> PASS, GO/NO-GO: GO
frontend GitHub Actions run 27079644677 -> success; typecheck, legacy guard, lint, build, Cloudflare Pages deploy, OG/static checks, Tenant 1 E2E passed
```

Read-only production safety check:

```text
Tenant 1 controlled recipient 01031217466 is unavailable for signup approval canary:
- student id=1890, name=[E2E-ClinicNoticeVerify-20260601031458], parent_phone=01031217466
- student id=1201, name=E2E알림톡테스트, phone=01031217466, parent_phone=01031217466
```

## 6. Current Closure Criteria

Broad promotion remains no-go until all P0 rows in
`student-domain-launch-readiness.md` are `passed` or explicitly accepted as a
documented exception. The largest open item is not code capability; it is safe
production execution of account/signup notification canaries without using real
student or parent phone numbers.
