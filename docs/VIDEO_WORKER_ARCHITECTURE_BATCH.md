# Video Worker Architecture (AWS Batch)

> **최신 인프라 기준**

## 개요

Video 인코딩은 **DB(VideoTranscodeJob) SSOT** 기반 **AWS Batch**로 전환됨. SQS는 인코딩 경로에서 사용하지 않음.

## 흐름

```
1. Upload 완료 → Video.status = UPLOADED
2. create_job_and_submit_batch(video) → VideoTranscodeJob(QUEUED) 생성
3. submit_batch_job(job_id) 호출
4. AWS Batch Job 제출
5. Batch 컨테이너: job_id 인자 → (SUCCEEDED면 exit) → (READY+hls_path면 job_complete 후 exit) → process_video → job_complete → exit
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
| Batch 엔트리포인트 | `apps/worker/video_worker/batch_main.py` |
| API 플로우 | `apps/support/video/views/video_views.py` → create_job_and_submit_batch |
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
| Batch retry | 비활성화 (retryStrategy.attempts=1) |

## delete_r2 (R2 비동기 삭제)

- `enqueue_delete_r2` → SQS **academy-video-delete-r2**
- 소비자: `scripts/infra/delete_r2_lambda_setup.ps1`로 배포한 Lambda (SQS 트리거)

## 인프라

| 항목 | 스크립트/경로 |
|------|---------------|
| 전체 설정 (권장) | `scripts/infra/batch_video_setup_full.ps1` |
| 개별 설정 | `scripts/infra/batch_video_setup.ps1` |
| retryStrategy 검증 | `scripts/infra/batch_video_verify_and_register.ps1` |
| IAM | `scripts/infra/iam/` (trust_*, policy_video_job_role.json) |
| Batch JSON | `scripts/infra/batch/` (video_compute_env, job_queue, job_definition) |

## 환경 변수 (API)

- `VIDEO_BATCH_JOB_QUEUE`: academy-video-batch-queue
- `VIDEO_BATCH_JOB_DEFINITION`: academy-video-batch-jobdef
- `AWS_REGION` / `AWS_DEFAULT_REGION`

## 환경 변수 (Batch 컨테이너)

- `VIDEO_JOB_ID`: Batch parameters의 job_id (command 인자로 전달)
- R2, DB, Redis: Job Definition에서 SSM Parameter Store secrets 또는 env로 전달
