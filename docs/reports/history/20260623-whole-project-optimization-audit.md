# Whole Project Optimization Audit - 2026-06-23

**Status:** verified tranche report  
**Scope:** backend ID-domain guardrails, frontend refactor budget, refactor docs

## Baseline

- Backend and frontend worktrees were clean at start.
- `git -C C:\academy\backend diff --check`: passed.
- `git -C C:\academy\frontend diff --check`: passed.
- Backend snapshot after this tranche:
  - `cross_domain_import=116`
  - `cross_domain_internal_import=606`
  - `domain_infra_import=82`
  - `check_id_domain_safety.py`: 52 warning(s), 0 error(s)
- Frontend snapshot after this tranche:
  - `same_app_domain_import=150`
  - `large_frontend_file=34`
  - `e2e_wait_for_timeout=34`
  - `local_format_defs=121`
  - `status_map_defs=35`
  - `query_key_literals=901`
  - `api_response_type_defs=102`
  - `pnpm refactor:budget`: passed

## Tranche 1 Changes

- Converted `NotificationLog.source_tenant_id` to a `Tenant` ForeignKey while
  preserving the `source_tenant_id` DB column and public `.source_tenant_id`
  access.
- Converted OMR fact references to ForeignKey fields:
  - `OMRDetectedAnswer.exam_question` uses the existing `exam_question_id`
    column.
  - `OMRStudentMatch.enrollment` uses the existing `enrollment_id` column.
- Stopped storing raw OMR question numbers in `exam_question_id` when the sheet
  question-number-to-PK map is unavailable. `question_number` remains the raw
  worker question number.
- Added data migrations that null invalid legacy source tenant, OMR question,
  and OMR enrollment references before FK constraints are applied.
- Consolidated repeated frontend display text helpers into
  `frontend/src/shared/utils/displayText.ts`.
- Folded dev tenant usage/activity/storage query keys into the existing tenant
  key factory.
- Renamed a few internal frontend raw payload/result types so hand-authored
  `Response`/`Dto` suffix debt stays within the committed refactor budget.

## Verification

- Backend:
  - `python manage.py check --settings apps.api.config.settings.test`: passed.
  - `python manage.py makemigrations --check --dry-run --settings apps.api.config.settings.test`: passed.
  - `python scripts\lint\check_id_domain_safety.py`: passed with warnings only.
  - `python -m ruff check <touched backend files>`: passed.
  - `python -m pytest apps/domains/submissions/tests/test_omr_dispatcher_sheet_resolution.py -v --tb=short`: 16 passed.
  - `python -m pytest apps/domains/messaging/tests/test_notification_log_redaction.py tests/test_messaging_queue_policy.py -v --tb=short`: 10 passed.
- Frontend:
  - `pnpm refactor:budget`: passed.
  - `pnpm typecheck`: passed.
  - `pnpm guard:legacy-api`: passed.

## Next Tranche Candidates

- Backend: reduce `UNORDERED_FIRST` warnings in ID-sensitive paths, starting
  with AI callbacks and OMR/notification selectors where deterministic row
  selection affects tenant or student identity.
- Frontend: continue from same-app domain import hotspots:
  `app_admin/sessions -> lectures/homework/results/exams` and
  `app_admin/lectures -> scores/students/videos/messages`.
- Contract rail: decide the OpenAPI generation path, then introduce generated
  frontend types for new or touched endpoints instead of adding more handwritten
  response types.
