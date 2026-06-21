# Matchup Segmentation QA Runbook

Tenant 2 matchup recovery의 재현 절차와 합격 기준이다. 목표는 테스트 우회가 아니라 실제 문항 분리 성공을 감사 결과와 육안 검수로 확인하는 것이다.

Related proposed backlog: `../../refactor/matchup-segmentation-risk-backlog.md`.

## Scope

- 대상: tenant 2의 과거 실사용 자료 중 손촬영 사진을 제외한 PDF, 스캔본, 텍스트 PDF.
- 제외: `student_exam_photo` 손촬영 사진. 이 유형은 별도 사진 보정/촬영 품질 게이트가 필요하다.
- 원칙: read-only 감사로 DB/R2 write 없이 현재 dispatcher 결과를 재현한다.

## Current Baseline

2026-06-20 v55 full-display 감사 기준:

- Deployed backend commit: `5047c59d3`
- Manifest: `C:\academy\_artifacts\sessions\matchup-recovery-plan-2026-06-18\tenant2-api-manual-gt-manifest.json`
- Originals: `C:\academy\_artifacts\sessions\matchup-recovery-plan-2026-06-18\originals`
- Audit output: `C:\academy\_artifacts\sessions\matchup-recovery-plan-2026-06-18\manifest-audit-v55-full-display`
- Visual QA: `C:\academy\_artifacts\sessions\matchup-recovery-plan-2026-06-18\visual-qc-v55\contact_sheet.png`

Baseline result:

- `selected_docs=217`, `evaluated_docs=217`, `file_missing_docs=0`
- `page_count=8738`, `total_boxes=18009`, `expected_problem_rows=16193`
- Manual GT subset: `docs=61`, `gt_count=4678`, `matched_count=4667`, `missed_count=11`
- Physical GT: `physical_gt_count=4662`, `physical_matched_count=4662`, `physical_missed_count=0`, `physical_recall=1.0`
- Raw missed rows are duplicate GT rows: `duplicate_gt_row_count=16`, `duplicate_missed_count=11`
- `manifest_gt_missed` structural flag is absent.

## Re-run Audit

From `C:\academy\backend`:

```powershell
python manage.py matchup_manifest_segmentation_audit `
  --manifest "C:\academy\_artifacts\sessions\matchup-recovery-plan-2026-06-18\tenant2-api-manual-gt-manifest.json" `
  --input-dir "C:\academy\_artifacts\sessions\matchup-recovery-plan-2026-06-18\originals" `
  --output "C:\academy\_artifacts\sessions\matchup-recovery-plan-2026-06-18\manifest-audit-next-full-display" `
  --prediction-box-kind display `
  --no-overlays
```

Use overlays for sampled visual QA:

```powershell
python manage.py matchup_manifest_segmentation_audit `
  --manifest "C:\academy\_artifacts\sessions\matchup-recovery-plan-2026-06-18\tenant2-api-manual-gt-manifest.json" `
  --input-dir "C:\academy\_artifacts\sessions\matchup-recovery-plan-2026-06-18\originals" `
  --output "C:\academy\_artifacts\sessions\matchup-recovery-plan-2026-06-18\manifest-audit-next-overlays" `
  --prediction-box-kind display `
  --overlay-limit-docs 20 `
  --overlay-limit-pages 4
```

## Pass Criteria

Hard fail:

- `file_missing_docs > 0`
- `eval_exception` or `file_missing` in `manifest_structural_flag_counts`
- `ground_truth.physical_missed_count > 0`
- `ground_truth.physical_recall < 1.0`
- `manifest_gt_missed` appears in `manifest_structural_flag_counts`

Allowed with triage:

- Raw `missed_count > 0` only when every miss is explained by duplicate GT rows and `physical_missed_count == 0`.
- `manifest_gt_precision_low` when recall is perfect and extra boxes are visually non-destructive.
- Count-ratio flags when manual GT physical recall is perfect or the document has no GT and sampled overlays show usable crops.

## v55 Warning Triage

| Flag | v55 count | Meaning | Action |
|------|-----------|---------|--------|
| `over_expected_count` | 43 | More boxes than manifest expected rows. Usually subquestion/context split or manifest count under-report. | Check sampled overlays and precision; not a recall blocker. |
| `under_expected_count` | 30 | Fewer boxes than manifest expected rows. | If GT physical recall is 1.0, keep as manifest/count drift. For no-GT docs, sample visually. |
| `severe_under_expected_count` | 18 | Large expected-vs-box count gap. | Prioritize visual sampling, especially no-GT docs. |
| `manifest_gt_precision_low` | 13 | Extra boxes lower precision. | Accept only with `physical_missed_count=0`; tune later to reduce review burden. |
| `expected_positive_no_boxes` | 1 | Manifest expects rows but dispatcher found no boxes. | Inspect doc manually before next production use. |
| `many_unnumbered_boxes` | 1 | Boxes exist but numbering metadata is weak. | Confirm crops are usable; improve number assignment if needed. |

## Regression Gate

Minimum local gate after segmentation changes:

```powershell
python manage.py check --settings apps.api.config.settings.test
python manage.py makemigrations --check --dry-run --settings apps.api.config.settings.test
python -m ruff check apps/ academy/ tests/test_question_splitter_t2_fixes.py tests/test_segment_scan_layout.py
python -m pytest `
  tests/test_question_splitter_t2_fixes.py `
  tests/test_segment_scan_layout.py `
  apps/domains/matchup/tests/test_manifest_segmentation_audit.py `
  apps/domains/matchup/tests/test_matchup_manual_gt_eval.py `
  tests/test_matchup_search_cache.py `
  tests/test_matchup_split_ideal_scenarios.py `
  tests/test_matchup_isolation_policy_fix.py `
  tests/test_smoke.py `
  -v --tb=short -x
```

`tests/test_matchup_isolation_policy_fix.py` is PostgreSQL-only for the actual recommendation query because SQLite does not support the required JSONB containment operator. To run the true integration gate:

```powershell
$env:DJANGO_SETTINGS_MODULE = "apps.api.config.settings.test_pg"
python -m pytest tests/test_matchup_isolation_policy_fix.py -v --tb=short
```

## Post Deploy

After backend deploy or worker image change:

```powershell
pwsh scripts/v1/run-production-canary.ps1 -Mode PostDeploy -AwsProfile default -WriteReport
pwsh scripts/v1/run-deploy-verification.ps1 -AwsProfile default
```

Expected after the 2026-06-20 deploy: production canary `PASS=33 WARN=0 FAIL=0` and deploy verification `GO`.
