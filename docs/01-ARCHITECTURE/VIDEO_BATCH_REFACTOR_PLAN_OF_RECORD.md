# Video Worker → AWS Batch Refactor — Plan of Record

> **최신 인프라 상태 (고정)**

## 1. 현재 구조 요약

| 구성요소 | 파일/위치 | 역할 |
|---------|-----------|------|
| VideoTranscodeJob | `apps/support/video/models.py` | Job 상태: QUEUED, RUNNING, SUCCEEDED, FAILED, RETRY_WAIT, DEAD, CANCELLED |
| Video.status | models.py | UPLOADED → PROCESSING → READY/FAILED |
| create_job_and_submit_batch | `apps/support/video/services/video_encoding.py` | Job 생성 + AWS Batch 제출 |
| video_views | `apps/support/video/views/video_views.py` | upload_complete, retry → create_job_and_submit_batch |
| Batch Worker | `apps/worker/video_worker/batch_main.py` | Batch 컨테이너 엔트리포인트 → process_video → job_complete |
| process_video | `src/infrastructure/video/processor.py` | 다운로드→ffmpeg→R2 업로드 |
| repositories_video | `academy/adapters/db/django/repositories_video.py` | job_complete, job_fail_retry, job_mark_dead |
| delete_r2 | `apps/support/video/services/delete_r2_queue.py` | SQS academy-video-delete-r2 → Lambda |

### 인코딩 경로 (완료)

- **DB → Batch만** 사용. SQS 인코딩 경로 제거.
- video_encoding.py `create_job_and_submit_batch` → batch_submit.py `submit_batch_job`
- batch_main.py: job_claim/heartbeat 없음, stateless executor

### 레거시 (제거됨)

- `apps/worker/video_worker/sqs_main.py` — 인코딩 경로 삭제 (delete_r2는 Lambda)
- (해당 Video ASG 전용 스크립트는 삭제됨)
- academy-video-jobs SQS 인코딩 경로 미사용

---

## 2. 인프라 구성 (최신)

### A) Batch 인프라

| 리소스 | 이름/값 |
|--------|---------|
| Compute Environment | academy-video-batch-ce-v3 (SLR + ARM64 ECS AMI) |
| Job Queue | academy-video-batch-queue |
| Job Definition | academy-video-batch-jobdef |
| Log Group | /aws/batch/academy-video-worker |
| retryStrategy | `{"attempts": 1}` (필수) |
| timeout | 14400초 (4시간) |

### B) 스크립트

| 스크립트 | 역할 |
|----------|------|
| `scripts/infra/batch_video_setup_full.ps1` | VPC/Subnet/SG 자동 탐색 → setup → retryStrategy 검증 (권장) |
| `scripts/infra/batch_video_setup.ps1` | Batch 인프라 설정 (VpcId, SubnetIds, SecurityGroupId 필요) |
| `scripts/infra/batch_update_ce_ami.ps1` | ARM64 ECS AMI + SLR CE Blue-Green (기본: v3, 신규 시 v4 생성) |
| `scripts/infra/batch_video_verify_and_register.ps1` | Job Definition 등록 + retryStrategy.attempts==1 검증 |
| `scripts/infra/batch_ensure_ce_maxvcpus.ps1` | CE maxvCpus 보정 (기본: v3) |
| `scripts/infra/batch_ensure_ce_enabled.ps1` | CE ENABLED 확인/복구 (기본: v3) |
| `scripts/infra/batch_video_cleanup_legacy.ps1` | Video ASG/스케일링 레거시 정리 |

### C) delete_r2

- SQS: `academy-video-delete-r2`
- 소비자: `scripts/infra/delete_r2_lambda_setup.ps1`로 배포한 Lambda

---

## 3. 책임 분리

| 역할 | API | Batch Worker |
|------|-----|--------------|
| Job 생성 | ✅ VideoTranscodeJob(QUEUED) 생성, video.current_job 설정 | - |
| Submit | ✅ submit_batch_job(job_id) 호출 | - |
| 처리 | - | process_video → job_complete → exit 0 |
| Retry 판단 | - | Django scan_stuck_video_jobs |
| Retry 실행 | - | submit_batch_job |
| delete_r2 | enqueue_delete_r2 (SQS academy-video-delete-r2) | Lambda가 소비 |

---

## 4. Idempotency 규칙

1. **job.state == SUCCEEDED**: exit 0 (재실행 시 idempotent)
2. **video.status == READY && video.hls_path**: job_complete 호출 후 exit 0 (업로드 후 크래시/retry 안전)
3. **Batch retry**: 비활성화 (`retryStrategy.attempts=1`). Retry는 DB 레벨(scan_stuck_video_jobs)에서만

---

## 5. 인프라 설정 (SSOT)

- **Region**: ap-northeast-2
- **ECR**: `809466760795.dkr.ecr.ap-northeast-2.amazonaws.com/academy-video-worker:latest`
- **VPC/Subnet/SG**: batch_video_setup_full.ps1 자동 탐색 또는 직접 지정
- **환경 변수 (API)**: VIDEO_BATCH_JOB_QUEUE, VIDEO_BATCH_JOB_DEFINITION (또는 기본값)

### IAM 역할 (Batch Video)

| 역할 | 이름 | 용도 | 설정 스크립트/정책 |
|------|------|------|---------------------|
| **Job Role** | academy-video-batch-job-role | 태스크 내 앱(SSM, ECR, CloudWatch 등) | batch_video_setup.ps1 → iam/policy_video_job_role.json |
| **Execution Role** | academy-batch-ecs-task-execution-role | 이미지 pull, 로그 쓰기 | batch_video_setup.ps1 → AmazonECSTaskExecutionRolePolicy |
| **Instance Role** (CE) | academy-batch-ecs-instance-role | Batch CE EC2 → ECS 조인, ECR pull, 로그 | batch_attach_ecs_instance_role_policies.ps1 (instance profile) |
| **Batch Service Role** | academy-batch-service-role | Batch 서비스가 CE/Queue 관리 | batch_video_setup.ps1 → AWSBatchServiceRole |

### 운영 검증

- **Video 검증**: AWS Batch job 상태 + CloudWatch 로그 그룹 `/aws/batch/academy-video-worker`
- **배포 전 검증**: `python scripts/deployment_readiness_check.py --docker` — Video는 Docker 검증 제외(Batch 전용). Messaging/AI 워커 이미지만 검사.
