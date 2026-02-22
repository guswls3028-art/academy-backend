# Video Worker → AWS Batch Refactor — Plan of Record

## 1. 현재 구조 요약

| 구성요소 | 파일/위치 | 역할 |
|---------|-----------|------|
| VideoTranscodeJob | `apps/support/video/models.py` | Job 상태: QUEUED, RUNNING, SUCCEEDED, FAILED, RETRY_WAIT, DEAD, CANCELLED |
| Video.status | models.py | UPLOADED → PROCESSING → READY/FAILED |
| create_job_and_enqueue | `apps/support/video/services/sqs_queue.py` | Job 생성 + SQS enqueue |
| video_views | `apps/support/video/views/video_views.py` | upload_complete, retry → create_job_and_enqueue |
| SQS Worker | `apps/worker/video_worker/sqs_main.py` | SQS receive → job_claim → process_video → job_complete |
| process_video | `src/infrastructure/video/processor.py` | 다운로드→ffmpeg→R2 업로드 |
| repositories_video | `academy/adapters/db/django/repositories_video.py` | job_claim_for_running, job_complete, job_fail_retry |
| delete_r2 | sqs_main.py, video_views.py | 영상 삭제 시 R2 정리 (동일 SQS 큐) |

### 레거시 (제거 대상)

- `scripts/infra/apply_video_asg_scaling_policy.ps1`
- `scripts/video_worker_scaling_sqs_direct.ps1`
- `scripts/apply_video_worker_scaling_fix.ps1`
- `scripts/apply_video_visible_only_tt.ps1`, `apply_video_target_tracking.ps1`, `update_video_tt_target.ps1`
- `scripts/apply_video_mixed_instances.ps1` (video ASG 관련 부분)
- `scripts/video_worker_oneclick_setup.ps1` (video 경로)
- `scripts/redeploy/redeploy_video_worker.ps1`
- `scripts/collect_video_worker_incident_data.ps1`
- `infra/worker_asg/video-visible-tt.json`, `video_visible_only_tt.json`
- `apps/support/video/views/internal_views.py`: VideoBacklogCountView, VideoBacklogScoreView, VideoAsgInterruptStatusView (video ASG용)
- `academy-worker-queue-depth-metric` Lambda: video scaling path에서 이미 제외됨 (삭제하지 않음, AI/Messaging 영향)
- BacklogCount Redis 메트릭: video scaling 제거 시 불필요

---

## 2. 변경 대상 파일 목록

### A) 신규 생성

| 파일 | 역할 |
|------|------|
| `apps/support/video/services/batch_submit.py` | submit_batch_job(video_job_id) 서비스 |
| `apps/worker/video_worker/batch_main.py` | Batch 엔트리포인트 (JOB_ID env → process → exit) |
| `scripts/infra/iam/trust_batch_service.json` | Batch 서비스 역할 신뢰 정책 |
| `scripts/infra/iam/trust_ec2.json` | EC2 인스턴스 역할 신뢰 정책 |
| `scripts/infra/iam/trust_ecs_tasks.json` | ECS 태스크 역할 신뢰 정책 |
| `scripts/infra/iam/policy_video_job_role.json` | Batch Job 역할 (DB/SSM/R2/CloudWatch) |
| `scripts/infra/batch/video_compute_env.json` | Compute Environment |
| `scripts/infra/batch/video_job_queue.json` | Job Queue |
| `scripts/infra/batch/video_job_definition.json` | Job Definition |
| `scripts/infra/batch_video_setup.ps1` | Batch 인프라 설정 (idempotent) |
| `scripts/infra/batch_video_cleanup_legacy.ps1` | Video ASG/스케일링 정리 |
| `docs/VIDEO_WORKER_ARCHITECTURE_BATCH.md` | 아키텍처 문서 |

### B) 수정

| 파일 | 변경 내용 |
|------|----------|
| `apps/support/video/services/sqs_queue.py` | create_job_and_enqueue → create_job_and_submit_batch (SQS 대신 Batch) |
| `apps/support/video/views/video_views.py` | create_job_and_enqueue → create_job_and_submit_batch 호출 |
| `apps/support/video/urls.py` (필요 시) | - |
| `apps/support/video/views/internal_views.py` | VideoBacklogCountView, VideoBacklogScoreView, VideoAsgInterruptStatusView deprecated/제거 |
| `apps/worker/video_worker/` | sqs_main.py → batch_main.py 로 전환, Dockerfile CMD 수정 |
| `docker/worker/video/Dockerfile` | CMD batch_main.py |

### C) delete_r2 처리

- **인코딩**: SQS 제거 → Batch만 사용
- **delete_r2**: academy-video-jobs SQS 유지 + **Lambda** consumer (SQS 이벤트 소스)
- `scripts/infra/delete_r2_lambda_setup.ps1` (신규): delete_r2 전용 Lambda 설정

### D) 삭제/Deprecate

- `apps/worker/video_worker/sqs_main.py` → batch_main.py로 대체 (삭제 또는 deprecated)
- 위 "레거시" 스크립트들: DEPRECATED 주석 또는 삭제

---

## 3. 책임 분리

| 역할 | API | Batch Worker |
|------|-----|--------------|
| Job 생성 | ✅ VideoTranscodeJob(QUEUED) 생성, video.current_job 설정 | - |
| Submit | ✅ submit_batch_job(job_id) 호출 | - |
| 처리 | - | job_id env 읽기 → job_claim_for_running → process_video → job_complete |
| 실패 | - | job_fail_retry, 필요 시 job_mark_dead |
| delete_r2 | enqueue_delete_r2 (SQS) | - (Lambda가 소비) |

---

## 4. Idempotency 규칙

1. **이미 SUCCEEDED**: job_complete idempotent → exit 0
2. **RUNNING + 최근 heartbeat**: 동시 실행 방지 (Batch는 1 job = 1 container이므로 중복 없음)
3. **FAILED/RETRY_WAIT**: retry 허용 (새 Batch job submit)

---

## 5. SSOT 인프라 변수 (full_redeploy, deploy_worker_asg 기반)

- Region: `ap-northeast-2`
- VpcId: `vpc-0831a2484f9b114c2`
- SubnetIds: `subnet-07a8427d3306ce910` (추가 AZ 서브넷 있으면 comma-separated)
- SecurityGroupId: `sg-02692600fbf8e26f7`
- AccountId: `aws sts get-caller-identity`
- ECR: `{AccountId}.dkr.ecr.{Region}.amazonaws.com/academy-video-worker:latest`
