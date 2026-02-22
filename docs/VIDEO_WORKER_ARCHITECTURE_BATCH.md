# Video Worker Architecture (AWS Batch)

## 개요

Video 인코딩은 **DB(VideoTranscodeJob) SSOT** 기반 **AWS Batch**로 전환됨. SQS는 인코딩 경로에서 사용하지 않음.

## 흐름

```
1. Upload 완료 → Video.status = UPLOADED
2. VideoTranscodeJob 생성 (state=QUEUED)
3. submit_batch_job(job_id) 호출
4. AWS Batch Job 제출
5. Batch 컨테이너: JOB_ID 인자로 실행 → (SUCCEEDED면 exit) → process_video → job_complete → exit
6. 컨테이너 종료 (exit 0/1)
```

## 1 job = 1 container = exit

- Batch는 작업당 1개 컨테이너 실행, 완료 시 종료
- SQS Long Polling, visibility, heartbeat 불필요
- ASG 스케일링 불필요 (Batch가 vCPU 0~max 자동 관리)

## 주요 파일

| 역할 | 파일 |
|------|------|
| Job 제출 | `apps/support/video/services/batch_submit.py` |
| Batch 엔트리포인트 | `apps/worker/video_worker/batch_main.py` |
| API 플로우 | `apps/support/video/views/video_views.py` → create_job_and_submit_batch |
| Job Repository | `academy/adapters/db/django/repositories_video.py` |

## Idempotency

- **SUCCEEDED**: exit 0 (재실행 시 idempotent)
- **RUNNING + 최근 heartbeat**: Batch는 1 job = 1 container이므로 중복 없음
- **FAILED/RETRY_WAIT**: scan_stuck_video_jobs가 RETRY_WAIT 전환 후 submit_batch_job 호출

## delete_r2 (R2 비동기 삭제)

- `enqueue_delete_r2` → SQS academy-video-jobs (action=delete_r2)
- **소비자**: `scripts/infra/delete_r2_lambda_setup.ps1`로 배포한 Lambda (SQS 트리거)
- 인코딩 Batch 전환 후, delete_r2 메시지는 Lambda가 처리 (또는 sqs_main을 delete_r2 전용으로 별도 실행)

## 인프라

- **Setup**: `scripts/infra/batch_video_setup.ps1`
- **Legacy 정리**: `scripts/infra/batch_video_cleanup_legacy.ps1`
- **IAM**: `scripts/infra/iam/` (trust_*, policy_video_job_role.json)
- **Batch**: `scripts/infra/batch/` (video_compute_env, job_queue, job_definition)

## 환경 변수 (API)

- `VIDEO_BATCH_JOB_QUEUE`: academy-video-batch-queue
- `VIDEO_BATCH_JOB_DEFINITION`: academy-video-batch-jobdef
- `AWS_REGION` / `AWS_DEFAULT_REGION`

## 환경 변수 (Batch 컨테이너)

- `VIDEO_JOB_ID`: Batch parameters의 job_id (command 인자로 전달)
- R2, DB, Redis: Job Definition에서 SSM Parameter Store secrets 또는 env로 전달
  - 기존 worker env (R2_*, DATABASE_*, REDIS_* 등)를 Job Definition secrets에 매핑
