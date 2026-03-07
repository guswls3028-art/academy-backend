# 구버전 잔해 정리 (2026-03-07)

**목적:** Video Batch 인프라 구버전(academy-video-*) 참조 제거, v1(academy-v1-*) SSOT 정합성 확보

---

## 1. 수정된 파일

| 파일 | 변경 내용 |
|------|-----------|
| `apps/api/config/settings/base.py` | VIDEO_BATCH_COMPUTE_ENV_NAME, VIDEO_RECONCILE_RULE_NAME, VIDEO_SCAN_STUCK_RULE_NAME, VIDEO_OPS_JOB_DEF_* 추가. 기본값 v1 |
| `apps/support/video/management/commands/validate_video_system.py` | RECONCILE_RULE_NAME, SCAN_STUCK_RULE_NAME, OPS_JOB_DEF_* → v1 (settings 연동) |
| `apps/support/video/management/commands/validate_video_production_readiness.py` | RECONCILE_RULE, SCAN_STUCK_RULE, OPS_JOB_DEFS → v1 |
| `apps/support/video/management/commands/reconcile_batch_video_jobs.py` | docstring academy-v1-video-ops-queue 반영 |
| `.env.example` | VIDEO_BATCH_* → academy-v1-*, VIDEO_SQS_QUEUE_NAME 제거 (Batch 전용) |
| `scripts/v1/core/prune.ps1` | ASG 보호 패턴 *academy-v1-video-ops-ce*, *academy-v1-video-batch-ce* 추가 |
| `docs/00-SSOT/v1/reports/VIDEO-STUCK-DIAGNOSIS-AND-FIX.md` | 구버전 override 설명 제거, .env.example 기준으로 정리 |

---

## 2. v1 SSOT (params.yaml 기준)

| 리소스 | v1 이름 |
|--------|---------|
| Batch CE (standard) | academy-v1-video-batch-ce |
| Batch Queue (standard) | academy-v1-video-batch-queue |
| Batch JobDef (standard) | academy-v1-video-batch-jobdef |
| Batch CE (long) | academy-v1-video-batch-long-ce |
| Batch Queue (long) | academy-v1-video-batch-long-queue |
| Batch JobDef (long) | academy-v1-video-batch-long-jobdef |
| Ops CE | academy-v1-video-ops-ce |
| Ops Queue | academy-v1-video-ops-queue |
| Ops JobDef reconcile | academy-v1-video-ops-reconcile |
| Ops JobDef scanstuck | academy-v1-video-ops-scanstuck |
| EventBridge reconcile | academy-v1-reconcile-video-jobs |
| EventBridge scanstuck | academy-v1-video-scan-stuck-rate |

---

## 3. 유지되는 리소스 (v1 prefix 없음)

| 리소스 | 이름 | 비고 |
|--------|------|------|
| IAM Job Role | academy-video-batch-job-role | Batch JobDef에서 공통 사용, 변경 시 JobDef 전체 수정 필요 |
| Log Group (worker) | /aws/batch/academy-video-worker | 공통 |
| Log Group (ops) | /aws/batch/academy-video-ops | 공통 |

---

## 4. 제거된 구버전 참조

- `academy-video-batch-jobdef` (존재하지 않음)
- `academy-video-batch-queue`
- `academy-video-batch-ce`
- `academy-video-ops-reconcile` / `academy-video-ops-scanstuck`
- `academy-reconcile-video-jobs` / `academy-video-scan-stuck-rate` (EventBridge)
- `VIDEO_SQS_QUEUE_NAME` (.env.example, Video encoding = Batch 전용)
