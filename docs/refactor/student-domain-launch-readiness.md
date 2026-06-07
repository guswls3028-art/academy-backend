# Student Domain Launch Readiness

**Status:** [ACTIVE] pre-promotion gate
**Captured:** 2026-06-07 KST
**Owner SSOT:** `../domain/student-core.md`

This is the launch gate for the concern that student domain instability can
break every major product workflow. It is intentionally stricter than a normal
feature checklist.

## Decision

Student-domain technical launch gate is **GO for staged broad expansion** after
the 2026-06-07 KST pass. The account, signup Alimtalk, account recovery,
teacher/staff password reset, active-enrollment projection, production E2E gate,
video playback/progress, OMR backend real-use, OMR browser upload/review/regrade,
score-report, homework submit/grade, clinic remediation, beginner/misuse
guardrails, and cleanup probes passed on the real production target.

The recommended business action is not an unbounded traffic blast. Run promotion
as a staged ramp with the standard launch controls: provider balance/template
monitoring, worker health checks, E2E residue probe, and account/clinic canary
reruns after frontend/backend deploys. No known P0 student-domain blocker remains
for the launch decision captured here.

## Current Evidence From This Pass

Repo-confirmed:

- Student identity convergence exists in code through
  `apps/domains/students/services/identity.py`.
- Signup approval now routes durable transition through
  `approve_registration_request()`.
- Public account recovery and staff password reset share
  `apps/domains/students/services/account_recovery.py`.
- Public password reset protects the old password through
  `PendingPasswordReset`.
- Staff password reset can change the password immediately and rolls back on
  delivery failure.
- Student import row decisions are centralized in `import_students_from_rows()`
  and `resolve_student_import_row()`.
- OMR/result scope guards exist for tenant + active enrollment matching.
- Student-facing exam result detail and grades summary now use the canonical
  active-enrollment selector, so inactive enrollments no longer leak scores or
  homework summaries into the student app.
- Student-facing exam list/detail/submission, wrong-note, wrong-note PDF,
  exam-attempt history, attendance summary, session-hide mutations, dashboard
  scoped notices, and video/progress projections now fail closed through the
  canonical active-enrollment selector or its multi-student variant.
- Frontend student exam detail now avoids retrying 4xx responses, so direct
  access to a non-enrolled/non-accessible exam URL fails closed instead of
  lingering in a loading state.
- Frontend student API contract has a shared `students.ts` mapper used by admin
  and teacher flows.
- `frontend/e2e/flows/signup-approval-roundtrip.spec.ts` now covers public
  signup -> staff approval UI -> student login -> cleanup, with production
  recipient guards.
- Production bundle against production API additionally passed:
  `test:e2e:gate` (35 passed), score-report/session-assessment (2 passed), and
  counsel + exam + homework/scores/inventory + video/session bundle
  (37 passed, 2 conditional skips). E2E cleanup probe confirmed
  `E2E Test Exam` 0 and `[E2E] 상담 신청` 0 after final runs.
- Frontend E2E hardening now tracks counsel posts across retries and deletes
  test-only posts through an independent admin token, so a failed admin login
  attempt cannot leave `[E2E] 상담 신청 ...` residue behind.
- Production signup approval real-use canary passed after stale controlled-phone
  fixtures were cleaned:
  public signup -> admin approval UI -> student login -> cleanup -> provider log
  `id=2839`, `target_id=parent:1932:01031217466`.
- The signup approval canary now forces manual approval when Tenant 1
  `student_registration_auto_approve` is temporarily enabled, disables retries
  for the real-send run, restores the previous setting, and cleans accidental
  direct-create students if an unexpected `200` response is returned.
- Controlled recipient `01031217466` is available again after cleanup.
- Production video HLS playback chain passed for the persistent E2E student
  fixture (`student_id=1933`, enrollment `1052`, lecture `136`, session `159`,
  video `284`), including master/variant/segment fetch.
- Production video progress persistence passed by writing 42%, reading it back
  from the session video list, then resetting progress to 0 and confirming the
  reset.
- Backend OMR tenant real-use regression file passed: `31 passed`.
- OMR upload/review/regrade real-use canary passed in production with
  `frontend/e2e/admin/omr-review-realuse.spec.ts`: admin API fixture generated
  lecture/session/student/exam/answer key and real OMR PDF, admin browser UI
  uploaded the PDF, the worker persisted OMR answer rows, the OMR review
  workspace selected the student and saved corrected answers, regrade returned
  `60/100`, and student grades reflected the result.
- Clinic remediation real-use canary passed in production with
  `frontend/e2e/student/clinic-remediation-realuse.spec.ts`: failed student exam
  result created a ClinicLink target, student browser booked the clinic, staff
  approved/completed attendance, clinic retake passed `90/100`, the original
  failed result changed to remediated/final pass, `clinic_required=false`, the
  unresolved target disappeared, and student result/grades UI showed the
  remediated state.
- Backend clinic trigger was patched so a resolved source evidence row is not
  re-opened after `EXAM_PASS`, `HOMEWORK_PASS`, `MANUAL_OVERRIDE`, `WAIVED`, or
  `SOURCE_REMOVED`; regression test
  `test_exam_pass_resolution_does_not_recreate_failed_clinic_link` now covers
  the original bug.
- Account recovery real-use canary passed in production with
  `frontend/e2e/auth/account-recovery-realuse.spec.ts`: public login modal sent
  a real password-recovery Alimtalk to controlled recipient `01031217466`,
  account notification log reached `sent`, the old password stayed valid until
  temp activation, staff reset changed only the generated E2E student password,
  and the test restored/deleted its fixture.
- Account notification logs now have an explicit regression test proving account
  and password-reset message bodies are redacted from `NotificationLog`.
- Student abnormal-behavior guardrail passed in production with
  `frontend/e2e/student/student-domain-guardrails.spec.ts`: unauthenticated and
  fake-token student routes fail closed to the auth entry, logout plus browser
  back does not restore protected student data, and dashboard/grades/exams/
  clinic/submit/notifications do not overflow at a 390px mobile viewport.
- Student score-report real-use spec passed after cleanup was changed to use
  bulk delete/permanent delete instead of detail delete, avoiding withdrawal
  notification side effects for generated test students.
- Student homework submission real-use spec passed in production:
  admin API fixture created lecture/session/student/homework, student browser
  submitted a real file, admin scoring API wrote `92/100`, and student grades UI
  reflected the result.
- Lecture/session cleanup guard now ignores homeworks already removed from the
  session, so E2E cleanup can delete generated sessions/lectures after
  homework deletion without weakening history-preserving blockers.
- Post-deploy residue probe confirmed old homework canary leftovers
  `session=296` and `lecture=297` deleted with `204`, and active
  `[E2E-...] 과제체인` lectures/sessions/students were `0`.
- OMR residue probe confirmed active `[E2E-...] OMR` lectures, sessions, and
  students were `0`; inactive archived exams `399` and `400` are preserved
  result history and return the expected delete guard `403`.
- Student dashboard/mobile/narrow viewport bundle passed: `14 passed`.

Runtime-unverified but not a P0 launch blocker:

- Public account-recovery temporary-password activation cannot be fully automated
  from production without reading the recipient's Alimtalk body. This is
  intentional: the temporary password is not exposed through API responses or
  logs. Backend lifecycle tests cover temp login activation and must-change
  behavior; the production canary can additionally verify activation when
  `E2E_ACCOUNT_RECOVERY_TEMP_PASSWORD` is supplied from the controlled device.

## P0 Launch Gate

All P0 items must pass before promotion/expansion launch.

| Gate | Required proof |
|---|---|
| Account creation | admin single create, Excel/import create, and public signup approval create the same student graph |
| Password safety | public recovery keeps the old password until temporary-password login; staff reset rollback works on delivery failure |
| Account Alimtalk | exact owner templates enqueue and provider logs show success for controlled test recipient |
| Student identity | no-phone student uses parent-phone last 8 digits for OMR; malformed phone is rejected |
| Tenant isolation | admin/teacher/student cannot read or mutate another tenant's student, enrollment, submission, or result |
| Roster scope | OMR/results/homework/clinic resolve through active enrollment/session roster, not display name alone |
| Student projection | states created by admin/teacher are visible or hidden correctly in the student app |
| Cleanup | `[E2E-{timestamp}]` students, parents, enrollments, submissions, results, clinic rows, and notifications are cleaned up |

## P1 Real-Use Chains

Run these as real browser flows where possible. API setup may be used only for
fixtures that are not the behavior under review.

1. Public signup chain
   - public signup request;
   - admin approval;
   - student login with signup password;
   - profile and dashboard render;
   - cleanup.

2. Admin direct-create chain
   - admin creates student with and without student phone;
   - optional welcome Alimtalk path checked in logs;
   - student login;
   - OMR identifier visible in generated/recognized context;
   - cleanup.

3. Account recovery chain
   - username recovery generic response;
   - password recovery generic response;
   - pending reset created;
   - old password still works until activation;
   - temporary password login activates `must_change_password`;
   - unknown/ambiguous cases are generic no-op.

4. Teacher/staff password reset chain
   - teacher resets student by `ps_number`;
   - teacher resets parent by parent phone;
   - skip-notify requires authenticated active staff membership;
   - stale JWT cannot use privileged reset options;
   - account notification log is visible without secret body.

5. Assessment to clinic chain
   - create class/session/exam roster;
   - upload or submit OMR/answers;
   - grade result;
   - failed result becomes clinic target;
   - clinic attendance/remediation updates result projection;
   - student app reflects the final state.

6. Homework chain
   - create homework for a session roster;
   - student submits;
   - admin reviews/grades;
   - student sees the result and related feedback.

7. QnA/counseling/community chain
   - student creates content;
   - staff replies;
   - student sees reply;
   - no other tenant/student sees it.

## Required Command Groups

Backend P0:

```powershell
cd C:\academy\backend
python -m pytest apps\domains\students\tests\test_student_identity_convergence.py apps\domains\students\tests\test_registration_password_safety.py apps\domains\students\tests\test_password_reset_safety.py apps\domains\students\tests\test_account_recovery.py -v --tb=short -x
python -m pytest tests\test_student_video_progress_enrollment_resolution.py apps\domains\student_app\tests\test_parent_exam_child_selection.py apps\domains\student_app\tests\test_grades_summary_homework.py apps\domains\student_app\tests\test_session_tenant_isolation.py apps\domains\results\tests\test_security_regression.py apps\domains\students\tests\test_student_domain_stabilization.py apps\domains\results\tests\test_submission_scope_guard.py apps\support\omr\tests\test_candidate_matching.py -v --tb=short -x
python manage.py check --settings apps.api.config.settings.test
python manage.py makemigrations --check --dry-run --settings apps.api.config.settings.test
python -m ruff check apps/ academy/
```

Frontend P0:

```powershell
cd C:\academy\frontend
pnpm typecheck
pnpm guard:legacy-api
pnpm lint
pnpm build
pnpm test:e2e:gate
pnpm exec playwright test e2e\auth\account-recovery-modal.spec.ts --reporter=list
pnpm exec playwright test e2e\student\score-report-realuse.spec.ts e2e\admin\session-assessment-realuse.spec.ts --reporter=list
pnpm exec playwright test e2e\admin\omr-review-realuse.spec.ts --reporter=list
pnpm exec playwright test e2e\flows\counsel-roundtrip.spec.ts e2e\flows\exam-data-flow.spec.ts e2e\flows\homework-scores-inventory-data-flow.spec.ts e2e\flows\video-session-data-flow.spec.ts --reporter=list
pnpm exec playwright test e2e\student\homework-submission-realuse.spec.ts --reporter=list
```

Signup approval real-use canary:

```powershell
cd C:\academy\frontend
$env:E2E_ALLOW_SIGNUP_APPROVAL_REAL_SEND = "1"
$env:E2E_SIGNUP_CONTROLLED_PHONE = "01031217466"
pnpm exec playwright test e2e/flows/signup-approval-roundtrip.spec.ts --reporter=list
```

Do not run this in production until the controlled recipient is not already used
by an active student/parent fixture. As of the 2026-06-07 KST follow-up, stale
fixtures were removed and the canary passed with `01031217466`; keep the
pre-flight recipient availability check before every future real-send run. The
spec itself refuses production execution without the explicit allow flag and
exact controlled recipient.

Frontend E2E mode distinction:

- `pnpm test:e2e:gate` without overrides uses `.env.e2e` and validates the
  deployed production bundle at `https://hakwonplus.com`.
- To validate the current local bundle against the production API, run a
  separate Vite server with an explicit API proxy, then override
  `E2E_BASE_URL` for the Playwright run:

```powershell
cd C:\academy\frontend
$env:VITE_DEV_PROXY_TARGET = "https://api.hakwonplus.com"
pnpm exec vite --host 127.0.0.1 --port 5181 --strictPort

$env:E2E_BASE_URL = "http://127.0.0.1:5181"
pnpm test:e2e:gate
```

If production E2E fails but the local-bundle gate passes, classify it as a
deployment parity gap. Broad launch remains no-go until the frontend release is
deployed and the production-bundle gate is rerun successfully.

Real-use canary candidates:

```powershell
cd C:\academy\frontend
pnpm exec playwright test e2e\student\score-report-realuse.spec.ts --reporter=list
pnpm exec playwright test e2e\admin\session-assessment-realuse.spec.ts --reporter=list
pnpm exec playwright test e2e\admin\omr-review-realuse.spec.ts --reporter=list
pnpm exec playwright test e2e\flows\notice-roundtrip.spec.ts e2e\flows\qna-roundtrip.spec.ts --reporter=list
```

The canary list is not sufficient for launch until the gaps in
`frontend/docs/REAL-USE-E2E-INVENTORY.md` are promoted into real-use specs.

## No-Go Triggers

If any of these are true, do not launch broadly:

- account recovery returns different public responses for success vs unknown;
- password reset changes the real public password before temporary-password login;
- signup approval stores or sends plaintext password;
- staff reset can be invoked by stale JWT or inactive membership;
- OMR/result writes accept a deleted, inactive, or cross-tenant enrollment;
- student-facing result can show a draft/provisional state without an explicit
  product decision and test;
- admin/teacher UI success cannot be confirmed from the student or parent role;
- Alimtalk path has not been checked through provider/log evidence for the
  account triggers touched by the release;
- E2E data cleanup is missing.

## Backlog To Close The Gate

| Priority | Item | Disposition |
|---|---|---|
| P0 | Add or promote real-use signup approval E2E | passed in production with controlled real send to `01031217466`; latest verified log `id=2839`, `target_id=parent:1932:01031217466`; keep pre-flight duplicate check |
| P0 | Add account recovery activation E2E with pending reset proof | production public modal real-send + old-password proof + staff-reset restore passed; temp-password activation is backend-covered and optional in the canary when `E2E_ACCOUNT_RECOVERY_TEMP_PASSWORD` is supplied |
| P0 | Add OMR -> grading -> student result chain canary | passed in production with `frontend/e2e/admin/omr-review-realuse.spec.ts`; generated OMR PDF -> admin UI upload -> worker answer rows -> review/regrade `60/100` -> student grades projection; active residue `0`, inactive result-history exams `399`/`400` preserved by delete guard |
| P0 | Add clinic remediation chain canary | passed in production with `frontend/e2e/student/clinic-remediation-realuse.spec.ts`; failed exam -> target -> student booking -> staff completion -> retake pass -> `clinic_required=false` and student UI remediated |
| P1 | Add homework submission chain canary | passed in production with `e2e/student/homework-submission-realuse.spec.ts`; generated homework, submission, score, student projection, cleanup, and residue probe completed |
| P1 | Add account Alimtalk controlled-send runbook evidence template | signup approval provider/log path proved for controlled recipient; public account-recovery body/device confirmation still needs manual/provider validation |
| P1 | Audit student result visibility for final/draft/provisional policy | active/inactive enrollment projection covered for student detail, grades summary, exam list/submission, video/progress, wrong-note/PDF, attempt history, attendance, and session hide; final/draft policy still needs product decision |
| P1 | Add mobile/narrow viewport review for student/admin account screens | student dashboard/mobile/narrow bundle and student-domain guardrail passed; admin/teacher account screens still need visual validation |

## Reporting Standard

Launch-readiness reports must classify every item:

- `passed`
- `failed`
- `runtime-unverified`
- `needs-manual-validation`
- `skipped-not-touched`

Do not summarize this gate as "ready" until every P0 item is `passed` or has a
documented launch exception. As of v1.2.41, the remaining notes above are
operational/manual-validation items, not known P0 code blockers.
