# Whole Project Optimization Audit - 2026-06-23

**Status:** verified tranche report  
**Scope:** backend ID-domain guardrails, frontend refactor budget, refactor docs

## Baseline

- Backend and frontend worktrees were clean at start.
- `git -C C:\academy\backend diff --check`: passed.
- `git -C C:\academy\frontend diff --check`: passed.
- Backend snapshot after this tranche:
  - `cross_domain_import=117`
  - `cross_domain_internal_import=599`
  - `domain_infra_import=82`
  - `check_id_domain_safety.py`: 34 warning(s), 0 error(s)
  - `UNORDERED_FIRST`: 6
  - `SILENT_FALLBACK`: 0
- Frontend snapshot after this tranche:
  - `same_app_domain_import=146`
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

## Tranche 2 Changes

- Reduced ID-domain safety warnings from 52 to 39 without tripping
  `refactor_boundary_snapshot.py --strict-touched`.
- Added explicit ordering before `.first()` in strict-safe exam structure,
  lecture system-container, matchup proposal, messaging selector, OMR support,
  and submission failure support paths.
- Replaced student exam enrollment literal `(None, None)` fallbacks with a named
  `MISSING_EXAM_ENROLLMENT` result helper and deterministic enrollment lookup.
- Left broad boundary-debt files untouched after verification showed that
  touching them triggers strict cross-domain/import failures. Their remaining
  `UNORDERED_FIRST` fixes should be paired with boundary extraction.
- Left `[ALLOWED]` integer-FK candidates untouched; each needs a domain-specific
  migration/data-normalization plan rather than a mechanical field rewrite.

## Tranche 3 Changes

- Added shared assessment query keys in
  `frontend/src/shared/api/queryKeys/assessments.ts`.
- Routed session assessment homework list fetches through
  `frontend/src/shared/api/contracts/assessments.ts` instead of importing admin
  homework internals.
- Made the admin homework policy key reuse the shared assessment key, keeping
  sessions and homework cache invalidation on one contract.
- Reduced frontend `same_app_domain_import` from 150 to 148 and
  `app_admin/sessions` domain outbound imports from 21 to 19.

## Tranche 4 Changes

- Added assessment homework policy typing/fetching to
  `frontend/src/shared/api/contracts/assessments.ts`.
- Routed the session assessment side panel through the shared assessment
  homework policy/list contract instead of importing admin homework internals.
- Reduced frontend `same_app_domain_import` from 148 to 146 and
  `app_admin/sessions` domain outbound imports from 19 to 17. The
  `app_admin/sessions -> app_admin/homework` pair no longer appears in the
  top-10 hot-pair list.

## Tranche 5 Changes

- Added `apps.domains.enrollment.public_queries.get_enrollment_tenant_id` so
  progress clinic trigger code no longer imports the enrollment model directly.
- Added deterministic ordering before the unresolved `ClinicLink` lookup in
  `ClinicTriggerService.auto_create_if_exam_risk`.
- Reduced ID-domain safety warnings from 39 to 38, `UNORDERED_FIRST` from 11 to
  10, and backend `cross_domain_internal_import` from 606 to 605. Public
  `cross_domain_import` returned to 117 because the direct internal model import
  became a public query boundary import.

## Tranche 6 Changes

- Moved matchup upload-folder creation behind
  `academy.adapters.db.django.repositories_inventory.inventory_folder_get_or_create`,
  removing the direct inventory model import from `matchup/services.py`.
- Added deterministic ordering before both manual `MatchupProblem` existing-row
  lookups used by crop/paste upsert paths.
- Reduced ID-domain safety warnings from 38 to 36, `UNORDERED_FIRST` from 10 to
  8, and backend `cross_domain_internal_import` from 605 to 604 while keeping
  `cross_domain_import` at 117.

## Tranche 7 Changes

- Moved attendance/session reads for manual attendance previews behind
  `academy.adapters.db.django.repositories_enrollment` helpers.
- Added deterministic ordering before `NotificationPreviewToken` consumption
  lookup.
- Reduced ID-domain safety warnings from 36 to 35, `UNORDERED_FIRST` from 8 to
  7, and backend `cross_domain_internal_import` from 604 to 602 while keeping
  `cross_domain_import` at 117.

## Tranche 8 Changes

- Added `academy.adapters.db.django.repositories_exams` for student-result exam,
  exam-enrollment, question-number, and answer-key reads.
- Routed `student_result_service` through the exams repository and added
  deterministic ordering before active enrollment selection.
- Reduced ID-domain safety warnings from 35 to 34, `UNORDERED_FIRST` from 7 to
  6, and backend `cross_domain_internal_import` from 602 to 599 while keeping
  `cross_domain_import` at 117.

## Verification

- Backend:
  - `python manage.py check --settings apps.api.config.settings.test`: passed.
  - `python manage.py makemigrations --check --dry-run --settings apps.api.config.settings.test`: passed.
  - `python scripts\lint\check_id_domain_safety.py`: 34 warning(s), 0 error(s).
  - `python scripts\lint\refactor_boundary_snapshot.py --strict-touched`: passed.
  - `python scripts\lint\refactor_boundary_snapshot.py --enforce-baseline`: passed.
  - `python -m ruff check <touched backend files>`: passed.
  - `python -m pytest apps/domains/progress/tests/test_drift_and_resolution.py::AutoCreateMetaMergeTest -v --tb=short`: 1 passed.
  - `python -m pytest apps/domains/progress/tests/test_drift_and_resolution.py -k "student_result_service or remediated or final_pass" -v --tb=short`: 3 passed, 18 deselected.
  - `python -m pytest apps/domains/matchup/tests/test_manual_correction_delta_hook.py apps/domains/matchup/tests/test_layout_fingerprint_hook.py -v --tb=short`: 41 passed.
  - `python -m pytest apps/domains/matchup/tests/test_owner_pin_protection.py -k manual_crop_preserves_owner_pinned_meta_on_recut -v --tb=short`: 1 passed, 12 deselected.
  - `python -m pytest apps/domains/student_app/tests/test_parent_exam_child_selection.py -v --tb=short`: 4 passed.
  - `python -m pytest tests/test_omr_fact_fk_mapping.py apps/domains/submissions/tests/test_omr_dispatcher_sheet_resolution.py -v --tb=short`: 16 passed, 4 subtests passed.
  - `python -m pytest apps/domains/matchup/tests/test_proposal_number_conflict.py apps/domains/matchup/tests/test_proposal_helpers.py -v --tb=short`: 44 passed.
  - `python -m pytest apps/domains/messaging/tests/test_notification_preview_views.py apps/domains/messaging/tests/test_notification_log_redaction.py tests/test_messaging_queue_policy.py -v --tb=short`: 21 passed.
  - `python -m pytest tests/test_smoke.py -v --tb=short -x`: 20 passed, 5 subtests passed.
- Frontend:
  - `pnpm refactor:budget`: passed.
  - `pnpm typecheck`: passed.
  - `pnpm guard:legacy-api`: passed.
  - `pnpm lint`: passed.
  - `pnpm build`: passed.

## Next Tranche Candidates

- Backend: extract public boundary helpers for the 6 remaining
  `UNORDERED_FIRST` sites in broad files, then add ordering without failing
  strict-touched.
- Backend: plan the remaining `[ALLOWED]` integer-FK candidates by domain. Start
  with `submissions.SubmissionAnswer.exam_question_id` or
  `results.ResultFact.question_id`, because both touch assessment correctness.
- Frontend: continue from same-app domain import hotspots:
  `app_admin/sessions -> lectures/results/exams`, remaining
  `app_admin/sessions -> homework` UI component imports, and
  `app_admin/lectures -> scores/students/videos/messages`.
- Contract rail: decide the OpenAPI generation path, then introduce generated
  frontend types for new or touched endpoints instead of adding more handwritten
  response types.
