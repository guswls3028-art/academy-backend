# Student Domain Launch Readiness

**Status:** [ACTIVE] pre-promotion gate
**Captured:** 2026-06-07 KST
**Owner SSOT:** `../domain/student-core.md`

This is the launch gate for the concern that student domain instability can
break every major product workflow. It is intentionally stricter than a normal
feature checklist.

## Decision

Controlled/internal expansion is **GO** after the 2026-06-07 KST pass: the
student-domain account, signup Alimtalk, active-enrollment projection, production
E2E gate, video playback/progress, OMR backend real-use, score-report, and
cleanup probes passed on the real production target.

Broad public promotion or large external expansion is still **NO-GO / HOLD**
until the remaining full-chain items below are either passed or explicitly
accepted as documented launch exceptions. The product can receive narrow fixes,
controlled customer onboarding, and internal hardening; the blocked activity is
high-volume public promotion that increases real student/parent load before all
major student-linked content chains are proven end-to-end.

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
- Student score-report real-use spec passed after cleanup was changed to use
  bulk delete/permanent delete instead of detail delete, avoiding withdrawal
  notification side effects for generated test students.
- Student dashboard/mobile/narrow viewport bundle passed: `14 passed`.

Runtime-unverified or launch-exception-required in this pass:

- public account-recovery activation as a browser chain:
  modal -> pending reset -> old-password proof -> temporary-password login
  activation -> must-change gate;
- OMR upload -> match/review -> grading -> admin score board -> student result
  as a full browser chain;
- failed result -> clinic target -> clinic booking/attendance -> remediation;
- homework creation -> student submission -> admin grading -> student result
  as a full create/submit/grade browser chain. Render/API data-flow passed;
- broad beginner/misuse exploration across back/forward, double-submit, stale
  tabs, mobile keyboard, and repeated role switching.

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
pnpm exec playwright test e2e\flows\counsel-roundtrip.spec.ts e2e\flows\exam-data-flow.spec.ts e2e\flows\homework-scores-inventory-data-flow.spec.ts e2e\flows\video-session-data-flow.spec.ts --reporter=list
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
| P0 | Add account recovery activation E2E with pending reset proof | needs implementation |
| P0 | Add OMR -> grading -> student result chain canary | `frontend/e2e/student/score-report-realuse.spec.ts` and backend OMR tenant real-use tests passed; OMR upload/match/review browser chain remains separate |
| P0 | Add clinic remediation chain canary | needs implementation |
| P1 | Add homework submission chain canary | production render/API data-flow passed; full create-submit-grade browser chain still needed |
| P1 | Add account Alimtalk controlled-send runbook evidence template | signup approval provider/log path proved for controlled recipient; public account-recovery body/device confirmation still needs manual/provider validation |
| P1 | Audit student result visibility for final/draft/provisional policy | active/inactive enrollment projection covered for student detail, grades summary, exam list/submission, video/progress, wrong-note/PDF, attempt history, attendance, and session hide; final/draft policy still needs product decision |
| P1 | Add mobile/narrow viewport review for student/admin account screens | student dashboard/mobile/narrow bundle passed; admin/teacher account screens still need visual validation |

## Reporting Standard

Launch-readiness reports must classify every item:

- `passed`
- `failed`
- `runtime-unverified`
- `needs-manual-validation`
- `skipped-not-touched`

Do not summarize this gate as "ready" until every P0 item is `passed` or has a
documented user-approved exception.
