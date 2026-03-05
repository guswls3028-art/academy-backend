# Video Worker Architecture (AWS Batch)

> **현행 인프라 기준.** Video 인코딩 = Batch 전용. SQS/ASG 경로 없음.  
> **인프라 스펙·리소스 이름·성공 조건:** [00-SSOT/RESOURCE-INVENTORY.md](../00-SSOT/RESOURCE-INVENTORY.md) (현재 유효 SSOT). 과거 버전: [archive/deploy_legacy/VIDEO_WORKER_INFRA_SSOT_V1.md](../archive/deploy_legacy/VIDEO_WORKER_INFRA_SSOT_V1.md)

## 개요

Video 인코딩은 **DB(VideoTranscodeJob) SSOT** 기반 **AWS Batch**로 동작한다. SQS는 인코딩 경로에서 사용하지 않는다.

## 실행 경로

```
Batch 컨테이너 시작
  → ENTRYPOINT: python -m apps.worker.video_worker.batch_entrypoint
  → Command:     python -m apps.worker.video_worker.batch_main <job_id>
  → batch_entrypoint: SSM /academy/workers/env 로드 → exec batch_main
  → batch_main:  job_set_running → heartbeat 스레드 → process_video → job_complete / job_fail_retry
```

## DB 생명주기 (Batch 경로)

| 단계 | 주체 | 비고 |
|------|------|------|
| QUEUED | API (create_job_and_submit_batch) | submit_batch_job 성공 시 aws_batch_job_id 저장 |
| RUNNING | batch_main (job_set_running) | 트랜스코딩 시작 직후 호출. scan_stuck 동작을 위해 필수 |
| last_heartbeat_at | batch_main (job_heartbeat 스레드) | VIDEO_JOB_HEARTBEAT_SECONDS(기본 60초)마다 갱신 |
| SUCCEEDED | batch_main (job_complete) | 정상 완료 시 |
| RETRY_WAIT | batch_main (job_fail_retry) | 예외/SIGTERM 시. attempt_count 증가, 5회 이상이면 job_mark_dead |

## 인프라 실패 대응

| 대응 | 구현 |
|------|------|
| SIGTERM/SIGINT | batch_main에서 핸들러 등록 → job_fail_retry(job_id, "TERMINATED") 후 sys.exit(1) |
| Stuck (RUNNING + 오래된 heartbeat) | scan_stuck_video_jobs → RETRY_WAIT + submit_batch_job (mgmt command만. internal API는 재제출 없음) |
| Batch↔DB 부정합 (OOM/인스턴스 종료 등) | reconcile_batch_video_jobs (describe_jobs → DB 반영, 선택 --resubmit) |

## 흐름 요약

```
1. Upload 완료 → Video.status = UPLOADED
2. create_job_and_submit_batch(video) → VideoTranscodeJob(QUEUED) 생성
3. submit_batch_job(job_id) 호출, aws_batch_job_id 저장
4. AWS Batch Job 제출
5. 컨테이너: job_set_running → heartbeat 시작 → process_video → job_complete 또는 job_fail_retry
6. 컨테이너 종료 (exit 0/1)
```

## 1 job = 1 container = exit

- Batch는 작업당 1개 컨테이너 실행, 완료 시 종료
- SQS Long Polling, visibility, heartbeat 불필요
- ASG 스케일링 불필요 (Batch가 vCPU 0~max 자동 관리)

## 주요 파일

| 역할 | 파일 |
|------|------|
| Job 생성 + 제출 | `apps/support/video/services/video_encoding.py` → create_job_and_submit_batch |
| Batch 제출 | `apps/support/video/services/batch_submit.py` |
| Batch 엔트리포인트 | `apps/worker/video_worker/batch_entrypoint.py` (SSM → exec batch_main) |
| Batch 워커 메인 | `apps/worker/video_worker/batch_main.py` (RUNNING/heartbeat/SIGTERM 포함) |
| Stuck 스캐너 | `apps/support/video/management/commands/scan_stuck_video_jobs.py` |
| Batch↔DB 정합성 | `apps/support/video/management/commands/reconcile_batch_video_jobs.py` |
| Job Repository | `academy/adapters/db/django/repositories_video.py` |

## Idempotency

1. **job.state == SUCCEEDED**: exit 0
2. **video.status == READY && video.hls_path**: job_complete 호출 후 exit 0 (process_video 건너뜀)
   - 업로드 후 컨테이너 크래시
   - AWS Batch retry
   - ffmpeg 중복 실행 방지
3. **FAILED/RETRY_WAIT**: scan_stuck_video_jobs가 submit_batch_job 호출

## Retry 계약

| 책임 | 주체 |
|------|------|
| Retry 판단 | Django scan_stuck_video_jobs |
| Retry 실행 | submit_batch_job |
| 중복 실행 방지 | READY idempotency guard |
| Batch retry | 비활성화 (retryStrategy.attempts=1, SSOT V1 §3) |

## delete_r2 (R2 비동기 삭제)

- `enqueue_delete_r2` → SQS **academy-video-delete-r2**
- 소비자: `scripts/infra/delete_r2_lambda_setup.ps1`로 배포한 Lambda (SQS 트리거)

## 인프라

| 항목 | 스크립트/경로 |
|------|---------------|
| **SSOT (스펙·이름·성공 조건)** | [00-SSOT/RESOURCE-INVENTORY.md](../../00-SSOT/RESOURCE-INVENTORY.md) |
| 원테이크 (권장) | `scripts/infra/infra_full_alignment_public_one_take.ps1` |
| 개별 설정 | `scripts/infra/batch_video_setup.ps1` |
| IAM | `scripts/infra/iam/` |
| Batch JSON | `scripts/infra/batch/` (video_compute_env, job_queue, job_definition) |

## 환경 변수 (API)

- `VIDEO_BATCH_JOB_QUEUE`: academy-video-batch-queue
- `VIDEO_BATCH_JOB_DEFINITION`: academy-video-batch-jobdef
- `AWS_REGION` / `AWS_DEFAULT_REGION`

## 환경 변수 (Batch 컨테이너)

- `VIDEO_JOB_ID`: Batch parameters의 job_id (command 인자로 전달)
- `VIDEO_JOB_HEARTBEAT_SECONDS`: heartbeat 간격(초, 기본 60)
- `VIDEO_JOB_MAX_ATTEMPTS`: 최대 시도 횟수(기본 5)
- R2, DB, Redis: Job Definition에서 SSM Parameter Store secrets 또는 env로 전달

---

## 더 보기

- **인프라 SSOT (프로덕션 기준):** [00-SSOT/RESOURCE-INVENTORY.md](../../00-SSOT/RESOURCE-INVENTORY.md)
- **실행 순서:** [00-SSOT/RUNBOOK.md](../../00-SSOT/RUNBOOK.md), [02-OPERATIONS/video_batch_production_runbook.md](../../02-OPERATIONS/video_batch_production_runbook.md)
- **워커 패키지 설명**: `apps/worker/video_worker/README.md`
- **스케일링/레거시 구분**: [VIDEO_WORKER_SCALING_SSOT.md](VIDEO_WORKER_SCALING_SSOT.md)
