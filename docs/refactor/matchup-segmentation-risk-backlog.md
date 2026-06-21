# Matchup Segmentation Hidden Risk Backlog

**Status:** [PROPOSED] execution backlog
**Created:** 2026-06-21 KST
**Scope:** Matchup question segmentation after Tenant 2 non-photo recovery
**Current baseline:** `docs/releases/v1.4.3.md` and `docs/operations/runbooks/matchup-segmentation-qa.md`

This document does not redefine the current production baseline. It records
hidden bug candidates, potential failure classes, and execution units for the
next stabilization pass. Current behavior remains governed by executable code,
tests, release notes, and the QA runbook.

## Truth Sources Inspected

| Source | Disposition | Notes |
|--------|-------------|-------|
| `docs/releases/v1.4.3.md` | repo-confirmed | Tenant 2 non-photo corpus closed with physical GT recall 1.0 and physical_missed 0. |
| `docs/operations/runbooks/matchup-segmentation-qa.md` | repo-confirmed | Defines rerun commands, hard fail criteria, and v55 warning triage. |
| `docs/domain/matchup.md` | repo-confirmed | Defines current product rule: page-as-problem and silent empty output are not acceptable final quality paths. |
| `academy/domain/tools/question_splitter.py` | repo-confirmed | Current deterministic splitter includes non-question gates, dense owner/subrow logic, anchor extraction, and layout fallback paths. |
| `academy/application/use_cases/ai/pipelines/matchup_pipeline.py` | repo-confirmed | Current pipeline routes source_type, page role filtering, VLM gates, counter fallback, and quality metadata. |
| `tests/test_question_splitter_t2_fixes.py` | repo-confirmed | Locks Tenant 2 anchor, non-question, color workbook, dense owner, and scan/layout regressions. |
| `tests/test_matchup_split_ideal_scenarios.py` | repo-confirmed | Locks VLM/page-role/source_type failure classes from earlier real incidents. |
| `docs/reports/runtime-images.latest.md` | repo-confirmed | Current API runtime image digest matches CI digest on both API instances. |

## Confirmed Baseline

- Tenant 2 historical real-use PDF/scan/text-PDF corpus excluding hand-shot
  photos is production-closed as of v1.4.3.
- Full manifest audit covered 217 documents and 8,738 pages.
- Manual GT physical metrics are `physical_gt_count=4662`,
  `physical_matched_count=4662`, `physical_missed_count=0`,
  `physical_recall=1.0`.
- The remaining raw GT misses are duplicate/overlapping GT rows, not current
  physical split misses.
- Hand-shot photos remain out of scope until a separate photo quality and
  perspective-correction gate exists.

## Hidden And Potential Bug Register

| ID | Risk | Current Evidence | Failure Mode | Severity | Disposition |
|----|------|------------------|--------------|----------|-------------|
| R1 | Count drift hides missed pages in no-GT documents | Runbook still tracks `under_expected_count`, `severe_under_expected_count`, `expected_positive_no_boxes`, and `many_unnumbered_boxes` | A document without manual GT could be visually wrong while aggregate physical recall stays green | P1 | proposed |
| R2 | Manifest count is not the same unit as physical questions | v1.4.3 doc765 notes `expected=59`, `pred=38` after answer/explanation/dummy pages were excluded | Future reviewers may treat count delta as either false pass or false fail without physical-unit triage | P1 | proposed |
| R3 | Page-role false positives return as new numbered concept formats | Tests lock many cover/concept/answer/explanation cases, but new textbook layouts can combine numbering with real prose | Concept, answer, appendix, or table pages become fake matchable problems | P1 | proposed |
| R4 | Dense owner/subrow logic is fixed for observed workbook families, not all families | Current dense owner logic targets fill-in/OX/numbered subrows under a parent stem | Passage-based, table-based, or math-proof owner questions may be split into fake top-level questions | P1 | proposed |
| R5 | Source type drift changes the whole routing path | Pipeline still has legacy fallback from `source_type=other` and title heuristics | Wrong upload intent can force workbook/photo/school-exam through the wrong splitter or VLM policy | P1 | proposed |
| R6 | Counter fallback can produce plausible but wrong numbering | Pipeline marks `number_source=counter_fallback`; school exam and numberless scan paths are known sensitive | Similarity/report rows attach to wrong question numbers even if crops look usable | P1 | proposed |
| R7 | Page-as-problem fallback can silently preserve bad quality | Domain doc says full-page fallback is not a final quality path; pipeline still gives page fallback some matching value | Bad segmentation becomes searchable and reportable instead of entering review | P1 | proposed |
| R8 | VLM configuration and cost gates are optional and easy to misunderstand | VLM adapter path depends on env and tenant/source gates; mock fallback must not drive production decisions | A future deploy may believe VLM protection is active when it is skipped, or may over-call expensive vision | P2 | proposed |
| R9 | Manual correction data is not yet a closed feedback loop | Domain doc proposes storing auto bbox plus final bbox; models for proposals/fingerprints exist | The same academy/workbook layout can fail repeatedly instead of learning from prior corrections | P2 | proposed |
| R10 | Observability does not yet alert on segmentation quality drift | Recent logs showed no errors, but quality warnings are mainly audit artifacts | Production can be technically healthy while segmentation quality degrades on a new corpus | P2 | proposed |
| R11 | Hand-shot photo exclusion can be bypassed by wrong metadata | Runbook excludes `student_exam_photo`, but manifest also has target/non-photo flags | A phone photo mislabeled as PDF/scan could be counted inside the non-photo baseline | P2 | proposed |
| R12 | Regression suite is strong for known cases but not generated from every audit warning | Tests include 115+ Tenant 2 fix cases and ideal scenarios, but warning rows are not automatically test fixtures | A fixed warning can regress without a named fixture if it never became a unit test | P2 | proposed |

## Design Principles

1. Treat segmentation as a quality pipeline, not a single splitter function.
2. Separate physical-question recall from manifest row-count drift.
3. Never convert uncertain segmentation into searchable/reportable problems
   without a low-quality marker or review state.
4. Use source_type and page-role as explicit contracts, not title-only
   inference.
5. Make every accepted residual risk reproducible by manifest row, document ID,
   page index, overlay, and regression test.
6. VLM should first verify or repair risky pages; it should not become an
   unbounded primary path without cost, tenant, and quality gates.

## Execution Units

### E1. Audit Warning Ledger

**Goal:** Turn the current manifest warning classes into a durable risk ledger.

**Work:**

- Extend or wrap `matchup_manifest_segmentation_audit` to emit a stable
  `risk-ledger.json` and `risk-ledger.md`.
- For each flagged document, store document ID, source_type, page indexes,
  warning flags, GT availability, physical_missed count, and overlay path.
- Classify each warning as `accepted_count_drift`, `needs_visual_sample`,
  `needs_fixture`, or `hard_fail`.

**Success criteria:**

- No warning row is explainable only by memory.
- `expected_positive_no_boxes` and `many_unnumbered_boxes` cannot be ignored
  without a documented visual decision.

**Verification:**

```powershell
python manage.py matchup_manifest_segmentation_audit `
  --manifest "C:\academy\_artifacts\sessions\matchup-recovery-plan-2026-06-18\tenant2-api-manual-gt-manifest.json" `
  --input-dir "C:\academy\_artifacts\sessions\matchup-recovery-plan-2026-06-18\originals" `
  --output "C:\academy\_artifacts\sessions\matchup-recovery-plan-2026-06-18\manifest-audit-risk-ledger" `
  --prediction-box-kind display
```

### E2. Quality Gate Before Indexing

**Goal:** Stop low-quality segmentation from silently entering vector search or
hit reports.

**Work:**

- Define a single quality contract for `indexable`, `review_required`,
  `low_quality_reason`, `page_as_problem`, `counter_fallback`, and
  `many_unnumbered`.
- Apply it at persist/index time, not only inside debug metadata.
- Block or review-gate documents with no boxes, full-page fallback, high
  counter-fallback ratio, or page-role uncertainty.

**Success criteria:**

- Bad segmentation creates review work, not false confidence.
- Existing v1.4.3 accepted physical cuts remain indexable.

**Verification:**

```powershell
python -m pytest `
  tests/test_matchup_split_ideal_scenarios.py `
  apps/domains/matchup/tests/test_manifest_segmentation_audit.py `
  apps/domains/matchup/tests/test_matchup_manual_gt_eval.py `
  -v --tb=short -x
```

### E3. Page-Role Classifier Consolidation

**Goal:** Make cover/index/concept/explanation/answer_key decisions consistent
across deterministic splitter, pipeline, VLM text filter, and audit.

**Work:**

- Document one canonical page-role enum and allowed transitions.
- Ensure deterministic `is_non_question_page`, VLM text role, and audit flags
  produce the same role names.
- Add fixture tests for all currently warned page-role failures.

**Success criteria:**

- A page classified as answer/explanation/cover/index cannot become a normal
  `MatchupProblem` without a manual override.
- New concept-page exceptions require a fixture.

**Verification:**

```powershell
python -m pytest `
  tests/test_question_splitter_t2_fixes.py `
  tests/test_matchup_vlm_gates.py `
  tests/test_matchup_split_ideal_scenarios.py `
  -v --tb=short -x
```

### E4. Source-Type Intake Hardening

**Goal:** Prevent metadata drift from choosing the wrong segmentation route.

**Work:**

- Add an intake audit for `source_type`, `document_role`, `upload_intent`,
  filename hints, MIME type, and manifest target flags.
- Flag contradictions such as `student_exam_photo` in a non-photo run,
  workbook-looking names with `other`, or answer/explanation documents marked
  indexable.
- Make post-upload source_type correction rerun segmentation only when the new
  route is meaningfully different.

**Success criteria:**

- A wrong source_type is visible before the next match report is generated.
- Tenant 2 non-photo audit cannot accidentally include hand-shot photos.

**Verification:**

```powershell
python -m pytest `
  tests/test_source_type_upload_precedence.py `
  tests/test_matchup_split_ideal_scenarios.py `
  apps/domains/matchup/tests/test_manifest_segmentation_audit.py `
  -v --tb=short -x
```

### E5. Dense Owner Generalization

**Goal:** Extend the v1.4.3 dense-owner fix from observed workbook pages to a
named strategy with measurable boundaries.

**Work:**

- Extract dense owner/subrow detection into a strategy object or small pure
  helper module if further cases appear.
- Add cases for passage owners, table owners, math-proof rows, and mixed
  OX/fill-in pages only when backed by real materials.
- Track owner-vs-subrow decisions in debug output for audit overlays.

**Success criteria:**

- New dense owner families are accepted only with real page evidence and tests.
- The existing color workbook and dense fill-in rows do not regress.

**Verification:**

```powershell
python -m pytest tests/test_question_splitter_t2_fixes.py -q
```

### E6. Selective VLM Verifier Rollout

**Goal:** Use VLM where deterministic segmentation is weak, without turning VLM
into an unbounded primary engine.

**Work:**

- Keep workbook VLM skip defaults unless an audited source_type requires it.
- Enable text page-role filter for risky pages before vision bbox calls.
- Add per-document and per-tenant call caps to verification reports.
- Record whether VLM was disabled, skipped by source_type, skipped by cost cap,
  or actually used.

**Success criteria:**

- Operators can tell whether VLM protected a document.
- VLM failures do not fall back to mock decisions in production.

**Verification:**

```powershell
python -m pytest tests/test_matchup_vlm_gates.py -v --tb=short -x
pwsh scripts/v1/run-deploy-verification.ps1 -AwsProfile default
```

### E7. Manual Correction Feedback Loop

**Goal:** Make manual cuts useful for the next automatic split.

**Work:**

- Ensure each manual correction stores original auto bbox, final bbox,
  page fingerprint, source_type, and reason.
- Build a read-only evaluator that replays past manual corrections against the
  current splitter.
- Promote repeated correction deltas into fixtures or layout profiles.

**Success criteria:**

- The same layout should not require identical manual fixes across multiple
  uploads.
- Manual correction deltas become regression tests or profile rules.

**Verification:**

```powershell
python -m pytest `
  apps/domains/matchup/tests/test_backfill_manual_correction_delta.py `
  apps/domains/matchup/tests/test_fingerprint_collector.py `
  apps/domains/matchup/tests/test_layout_fingerprint_hook.py `
  -v --tb=short -x
```

### E8. Segmentation Observability

**Goal:** Detect quality drift without waiting for a human to notice a bad
report.

**Work:**

- Emit structured counters for no-box documents, full-page fallback,
  non-question boxes, counter fallback ratio, VLM skipped/used, and
  review-required pages.
- Add deploy verification or a separate read-only report that summarizes recent
  segmentation quality by tenant/source_type.
- Keep exception logs separate from quality warnings; both are needed.

**Success criteria:**

- A production deploy can be green on health checks but still surface
  segmentation-quality warnings.
- Tenant-specific spikes are visible before match reports are sent.

**Verification:**

```powershell
pwsh scripts/v1/run-production-canary.ps1 -Mode PostDeploy -AwsProfile default -WriteReport
pwsh scripts/v1/run-deploy-verification.ps1 -AwsProfile default
```

### E9. New Corpus Admission Gate

**Goal:** Make "new material type" a deliberate admission event.

**Work:**

- Require a sample manifest and visual QA contact sheet before declaring a new
  source_type/layout family production-ready.
- Add at least one positive and one negative fixture for the new family.
- Record whether the new corpus is inside or outside the Tenant 2 non-photo
  baseline.

**Success criteria:**

- No new corpus inherits v1.4.3 success claims without audit evidence.
- Hand-shot photos remain explicitly out of scope until admitted by a separate
  gate.

**Verification:**

```powershell
python manage.py matchup_manifest_segmentation_audit `
  --manifest "<new-corpus-manifest.json>" `
  --input-dir "<new-corpus-originals>" `
  --output "<new-corpus-audit-output>" `
  --prediction-box-kind display
```

## Recommended Order

1. E1 Audit Warning Ledger
2. E2 Quality Gate Before Indexing
3. E4 Source-Type Intake Hardening
4. E3 Page-Role Classifier Consolidation
5. E5 Dense Owner Generalization only when new real pages require it
6. E6 Selective VLM Verifier Rollout
7. E7 Manual Correction Feedback Loop
8. E8 Segmentation Observability
9. E9 New Corpus Admission Gate

## Closure Criteria For This Backlog

- All P1 risks have either shipped fixes or documented acceptance with visual
  evidence.
- Audit warning rows are reproducible by document/page and no longer depend on
  memory.
- New segmentation changes cannot pass only by unit tests; they must also pass
  the manifest audit and sampled visual QA defined in the runbook.
- Production deploys can prove runtime image digest, service health, and
  segmentation quality signals separately.
