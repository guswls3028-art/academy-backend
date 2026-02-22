# Video Batch 인프라 상태 (최신 고정)

> 이 문서는 현재 인프라 구성을 기준으로 고정함.

## 실행 모델

| 항목 | 값 |
|------|-----|
| DB SSOT | VideoTranscodeJob |
| Executor | AWS Batch (stateless) |
| Retry | DB 레벨 (scan_stuck_video_jobs) |
| Batch retry | 비활성화 (attempts=1) |
| Idempotency | SUCCEEDED guard + READY guard |

## AWS 리소스

| 리소스 | 이름 |
|--------|------|
| Compute Environment | academy-video-batch-ce |
| Job Queue | academy-video-batch-queue |
| Job Definition | academy-video-batch-jobdef |
| Log Group | /aws/batch/academy-video-worker |
| SQS (delete_r2) | academy-video-delete-r2 |

## IAM Role

| Role | 용도 |
|------|------|
| academy-batch-service-role | Batch 서비스 |
| academy-batch-ecs-instance-role | EC2 인스턴스 |
| academy-batch-ecs-task-execution-role | ECS 태스크 (이미지 pull, 로그) |
| academy-video-batch-job-role | Job 실행 (DB, R2, SSM) |

## 스크립트 (실행 순서)

1. **batch_video_setup_full.ps1** — 인프라 없을 때 한 번에 설정
2. **batch_video_verify_and_register.ps1** — retryStrategy.attempts==1 검증/등록

## Job Definition 계약

```json
"retryStrategy": { "attempts": 1 }
```

- 필수. null 또는 attempts>1이면 이중 retry 위험.
