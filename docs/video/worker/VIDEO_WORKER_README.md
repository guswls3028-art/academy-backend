# Video Worker — 시작 문서

Video 인코딩 워커는 **AWS Batch 전용**이다. SQS/ASG 경로는 사용하지 않는다.

## 빠른 링크

| 목적 | 문서 |
|------|------|
| **아키텍처·실행 경로·DB 생명주기** | [VIDEO_WORKER_ARCHITECTURE_BATCH.md](VIDEO_WORKER_ARCHITECTURE_BATCH.md) |
| **코드 위치·검증 방법** | [apps/worker/video_worker/README.md](../../../apps/worker/video_worker/README.md) |
| **스케일링·레거시(ASG/SQS) 구분** | [VIDEO_WORKER_SCALING_SSOT.md](VIDEO_WORKER_SCALING_SSOT.md) |
| **프로덕션 체크리스트·테스트 시나리오** | [VIDEO_BATCH_PRODUCTION_MINIMUM_CHECKLIST_AND_ROADMAP.md](../batch/VIDEO_BATCH_PRODUCTION_MINIMUM_CHECKLIST_AND_ROADMAP.md) |
| **Spot/인프라 안전성 증거** | [VIDEO_BATCH_SPOT_AND_INFRA_SAFETY_EVIDENCE_REPORT.md](../batch/VIDEO_BATCH_SPOT_AND_INFRA_SAFETY_EVIDENCE_REPORT.md) |
| **서비스 런칭 설계(GPT용)** | [VIDEO_BATCH_SERVICE_LAUNCH_DESIGN_FOR_GPT.md](../batch/VIDEO_BATCH_SERVICE_LAUNCH_DESIGN_FOR_GPT.md) |

## 한 줄 요약

- **진입점**: Batch Job → `batch_entrypoint` → `batch_main <job_id>`
- **DB**: QUEUED → RUNNING(job_set_running) → heartbeat → SUCCEEDED(job_complete) 또는 RETRY_WAIT(job_fail_retry)
- **실패 대응**: SIGTERM 처리, scan_stuck_video_jobs, reconcile_batch_video_jobs
- **Deprecated**: `job_claim_for_running`(repositories_video) — SQS 경로용, 호출처 없음. RUNNING 반영은 `job_set_running`만 사용.
