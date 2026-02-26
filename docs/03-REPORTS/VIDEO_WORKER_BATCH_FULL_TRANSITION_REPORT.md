# Video 워커 ASG → Batch 100% 전환 현황 보고서

**작성 기준:** 코드베이스 및 문서 사실 기반  
**목적:** 완전 전환을 위한 설계도 및 잔존 정리·인프라 갭 파악

---

## 1. 요약

| 항목 | 상태 |
|------|------|
| **인코딩 실행 경로** | ✅ Batch 전용 (`create_job_and_submit_batch`만 사용, SQS 인코딩 경로 제거됨) |
| **워커 런타임** | ✅ `batch_main.py` / `batch_entrypoint.py`만 사용, `sqs_main.py` 삭제됨 |
| **배포 스크립트** | ✅ full_redeploy: Video = 빌드/ECR 푸시만, EC2/ASG 배포 없음. asgMap에 video 미포함 |
| **잔존 코드/문서** | ⚠️ Redis ASG 인터럽트, Lambda Video 메트릭/EC2 웨이크, deploy.ps1, check_workers.py 등 |
| **정책/인프라** | ⚠️ Batch CE/Job Role·문서화는 있으나, Lambda·ASG 레거시 정책 미정리, 일부 스크립트 주석 오류 |

---

## 2. 현재 아키텍처 (Batch 전용)

### 2.1 인코딩 경로 (사실)

- **API**: `video_views.py` → `create_job_and_submit_batch(video)` (upload_complete, retry 등)
- **서비스**: `video_encoding.py` → `create_job_and_submit_batch` → `batch_submit.submit_batch_job(job_id)`
- **설정**: `VIDEO_BATCH_JOB_QUEUE`, `VIDEO_BATCH_JOB_DEFINITION` (base.py, .env.example, check_batch_settings.py)
- **Batch 워커**: `apps/worker/video_worker/batch_main.py` → `process_video` → `job_complete` 후 exit 0
- **재시도**: DB 레벨만 (`scan_stuck_video_jobs` → `submit_batch_job`). Batch retryStrategy.attempts=1

### 2.2 Batch 인프라 (문서·스크립트 기준)

| 리소스 | 이름/값 |
|--------|---------|
| Compute Environment | academy-video-batch-ce-v3 (SLR + ARM64 ECS AMI) |
| Job Queue | academy-video-batch-queue |
| Job Definition | academy-video-batch-jobdef |
| Log Group | /aws/batch/academy-video-worker |
| retryStrategy | attempts=1 |
| timeout | 14400초(4시간) |

**스크립트:**  
`batch_video_setup_full.ps1`, `batch_video_setup.ps1`, `batch_video_verify_and_register.ps1`, `batch_update_ce_ami.ps1`, `batch_ensure_ce_maxvcpus.ps1`, `batch_ensure_ce_enabled.ps1`, `batch_attach_ecs_instance_role_policies.ps1`, `batch_video_cleanup_legacy.ps1`

---

## 3. 잔존 코드 (ASG/EC2 관련)

### 3.1 애플리케이션

| 위치 | 내용 | 비고 |
|------|------|------|
| `apps/support/video/redis_status_cache.py` | `VIDEO_ASG_INTERRUPT_KEY`, `set_asg_interrupt()`, `is_asg_interrupt()` | ASG scale-in drain 시 Lambda가 메트릭 퍼블리시 스킵용. Batch 전용화 후 사용처 없음 |
| `apps/support/video/views/internal_views.py` | `VideoAsgInterruptStatusView` → `is_asg_interrupt()` | `GET /api/v1/internal/video/asg-interrupt-status/` 응답. Lambda(BacklogCount)용으로 설계됨 |

**판단:** Video ASG 미사용 시에도 Lambda/내부 API 정리 전까지 유지한다는 문서(DEPLOY_AND_ASG_REMNANT_CHECK.md)와 일치. 완전 전환 시 Lambda 정리와 함께 제거 대상.

### 3.2 Lambda (인프라)

| 파일 | 내용 | 영향 |
|------|------|------|
| `infra/worker_asg/queue_depth_lambda/lambda_function.py` | `VIDEO_WORKER_ASG_NAME`, `academy-video-jobs` SQS visible+notVisible → `Academy/VideoProcessing` `VideoQueueDepthTotal` (Dimension: `AutoScalingGroupName=academy-video-worker-asg`) | Video는 Batch이므로 해당 메트릭은 고아. ASG가 없으면 TargetTracking 대상 없음. Lambda는 계속 퍼블리시 중 |
| `infra/worker_autoscale_lambda/lambda_function.py` | `VIDEO_WORKER_NAME_TAG`(academy-video-worker), `academy-video-jobs` visible → `maybe_start_worker(ec2, ssm, VIDEO_WORKER_NAME_TAG, ...)` | Video용 EC2 기동 로직 그대로 존재. Batch 전용이면 Video 분기 제거 필요 |

### 3.3 배포/운영 스크립트

| 파일 | 내용 | 비고 |
|------|------|------|
| `deploy.ps1` | 4대 EC2 원큐 배포에 `academy-video-worker` 포함. `REMOTE_CMDS`에 video용 docker pull/run | 레거시. Batch 전용이면 video 항목 제거 또는 “Batch용 미배포” 명시 |
| `scripts/build_and_push_ecr_on_ec2.sh` | VIDEO_WORKER_ONLY 완료 시 "로컬에서: full_redeploy.ps1 -WorkersViaASG -SkipBuild -DeployTarget video" | Video는 ASG 배포 없음. `-WorkersViaASG`는 AI/Messaging용. 주석 수정 권장 |
| `scripts/check_worker_logs.ps1` | "AI, Messaging ASG만. Video = Batch 전용"이지만 `$workerMap`에 academy-video-worker 포함, 하단에 "check_worker_logs.ps1 video", "docker logs -f academy-video-worker" 안내 | video 타입 넣으면 ASG가 없어 인스턴스 0건. Batch 로그는 CloudWatch에서만 확인 가능하다고 명시 필요 |
| `scripts/check_workers.py` | Video Worker 검증 시 `apps.worker.video_worker.sqs_main` import | **sqs_main.py는 삭제됨.** 해당 줄 실행 시 실패. `batch_main` 또는 Batch 전용 검증으로 변경 필요 |
| `scripts/deploy_preflight.ps1` | 인스턴스 이름에 academy-video-worker 포함 | 고정 EC2 4대 시나리오용. Batch만 쓰면 video는 선택 제외 가능 |
| `scripts/_config_instance_keys.ps1`, `deploy.ps1` | academy-video-worker 키/배포 정의 | 레거시 EC2 배포용 |
| `scripts/check_worker_docker.ps1` | nameFilter에 academy-video-worker 포함 | EC2 기준 필터. Batch만 쓰면 문서/옵션 정리 |

### 3.4 기타

- `scripts/infra/batch_video_cleanup_legacy.ps1`: Video ASG/스케일링 레거시 정리용. `VideoAsgName = academy-video-worker-asg` 참조.
- `git status` 기준 `batch_video_ce_bluegreen.ps1` 삭제됨. AMI/CE 업데이트는 `batch_update_ce_ami.ps1`로 이전된 상태.

---

## 4. 문서화 갭

### 4.1 ASG 중심 문서 (미갱신)

| 문서 | 내용 | 권장 |
|------|------|------|
| `docs/VIDEO_WORKER_SCALING_SSOT.md` | 상단에 "Video = AWS Batch 전용. 아래 Video ASG/SQS 스케일링 스크립트는 삭제됨. 참고용"이라고 했으나, 본문은 ASG·TargetTracking·apply_video_worker_scaling_fix.ps1 등 ASG 스케일링 설명 | Batch 전용 SSOT로 갱신하거나, “레거시(ASG) 참고용” 섹션과 “현행(Batch)” 섹션 분리 |
| `docs/VIDEO_ENTERPRISE_DRAIN_INTERRUPTION_SAFETY.md` | ASG scale-in, set_asg_interrupt, Lambda _is_asg_interrupt_from_api 등 | Batch 전용 전환 후에는 “과거 ASG 설계”로 표기하거나 경로 정리 |
| `docs/DEPLOY_AND_ASG_REMNANT_CHECK.md` | Redis ASG 인터럽트 유지 판단, Lambda 정리 시 함께 제거 | 유효. “완전 전환 시 Lambda/API 정리와 함께 제거” 항목으로 설계도에 반영 가능 |

### 4.2 Batch 중심 문서 (유지·보강)

- `docs/VIDEO_BATCH_REFACTOR_PLAN_OF_RECORD.md`: 인프라·스크립트·책임 분리 정리됨. **정책(IAM)** 은 CE instance profile 부여 스크립트만 명시, Job Role/Execution Role 권한(SSM, ECR, CloudWatch 등)은 한곳에 정리하면 좋음.
- `docs/VIDEO_BATCH_VERIFICATION_CHECKLIST.md`: Batch 전용 체크리스트 적절. full_redeploy/redeploy_worker_asg 요약 유지.

### 4.3 문서 정리 부족

- Batch Job Role / Execution Role 권한 목록이 PLAN_OF_RECORD나 별도 인프라 문서에 명시되어 있지 않음 (스크립트·코드에만 산재).
- Lambda 두 개(queue_depth, worker_autoscale)에서 Video 분기 제거 시 “변경 절차·배포·롤백” 문서 없음.

---

## 5. 정책·인프라 설정 갭

### 5.1 이미 적용된 것 (스크립트·문서 기준)

- Batch CE: academy-video-batch-ce-v3, ARM64 ECS AMI, SLR.
- Job Queue / Job Definition / retryStrategy=1 / timeout.
- `batch_attach_ecs_instance_role_policies.ps1`: CE instance profile에 ECS, ECR, CloudWatch 로그 권한 부여.
- `batch_ensure_ce_enabled.ps1`, `batch_ensure_ce_maxvcpus.ps1`: CE 상태·maxvCpus 보정.
- `batch_update_ce_ami.ps1`: CE AMI 갱신 및 Blue-Green(신규 CE 생성 후 큐 이동).

### 5.2 부족한 것

| 항목 | 현재 | 권장 |
|------|------|------|
| Batch Job Definition용 IAM Role | PLAN_OF_RECORD에 역할 이름·필수 정책 목록 미기재 | Job Role(SSM academy/*, ECR pull, CloudWatch Logs 등) 문서화 및 필요 시 스크립트로 부여 절차 고정 |
| Batch Execution Role | 문서에 명시 없음 | Fargate/EC2 launch type에 맞는 실행 역할·정책 문서화 |
| Lambda Video 분기 | queue_depth: Video 메트릭 계속 발행. worker_autoscale: Video EC2 기동 시도 | 전환 완료 정책 확정 후: (1) queue_depth에서 Video 퍼블리시 제거 또는 별도 “레거시 메트릭” 표기, (2) worker_autoscale에서 Video 분기 제거 및 배포 절차 문서화 |
| ASG academy-video-worker-asg | 스크립트/문서에서 “삭제됨” 또는 “미사용”으로 일관 표기 | 실제 계정에 리소스 잔존 시 제거 여부 결정 및 cleanup 스크립트와 문서 일치 |

### 5.3 선택 정리

- **Redis ASG 인터럽트**: Lambda/내부 API 제거 시 함께 제거 (이미 DEPLOY_AND_ASG_REMNANT_CHECK.md에 반영).
- **내부 API** `/internal/video/asg-interrupt-status/`, `/internal/video/backlog/` 등: Lambda 정리 시 사용처 없어지면 deprecated 또는 제거 후 문서 갱신.

---

## 6. 완전 전환을 위한 설계도(체크리스트)

### 6.1 코드

- [ ] **check_workers.py**: Video 검증을 `sqs_main` → `batch_main`(또는 Batch 전용 검증)으로 변경.
- [ ] **worker_autoscale_lambda**: Video 워커 분기 제거(또는 env로 비활성화). 배포·롤백 절차 문서화.
- [ ] **queue_depth_lambda**: Video 메트릭 발행 제거 또는 “레거시/미사용” 주석 및 문서 정리.
- [ ] **deploy.ps1**: academy-video-worker를 제거하거나 “Batch 전용, 이 스크립트로 배포하지 않음” 명시.
- [ ] **build_and_push_ecr_on_ec2.sh**: VIDEO_WORKER_ONLY 안내 문구에서 `-WorkersViaASG` 제거, “DeployTarget video 시 빌드/푸시만” 등으로 수정.
- [ ] **check_worker_logs.ps1**: video 타입 시 “Batch 전용, 로그는 CloudWatch /aws/batch/academy-video-worker” 안내로 통일.
- [ ] (선택) Redis `set_asg_interrupt`/`is_asg_interrupt` 및 `internal_views.VideoAsgInterruptStatusView`: Lambda 정리 후 제거.

### 6.2 문서

- [ ] **VIDEO_WORKER_SCALING_SSOT.md**: “현행 = Batch 전용” SSOT로 재작성. ASG 내용은 “레거시 참고”로 축소 이동.
- [ ] **VIDEO_BATCH_REFACTOR_PLAN_OF_RECORD.md**: Batch Job Role / Execution Role 권한 목록 및 적용 방법 추가.
- [ ] **VIDEO_ENTERPRISE_DRAIN_INTERRUPTION_SAFETY.md**: ASG 설계는 과거 참고로 표기.
- [ ] Lambda( queue_depth / worker_autoscale ) Video 변경 시 “변경 절차·배포·롤백” 문서 추가 또는 기존 운영 문서에 섹션 추가.

### 6.3 인프라·정책

- [ ] Batch Job Definition용 IAM Role 권한 목록 문서화 및 필요 시 스크립트로 고정.
- [ ] Lambda Video 분기 제거 시 배포 순서(예: queue_depth 먼저, 그 다음 worker_autoscale) 및 검증 단계 정리.
- [ ] AWS 계정 내 `academy-video-worker-asg` 등 Video ASG/관련 리소스 잔존 여부 확인 후, 유지 시 문서와 일치시키고 제거 시 cleanup 스크립트 실행 및 문서 반영.

### 6.4 배포·운영

- [ ] deploy_preflight / check_worker_docker 등에서 academy-video-worker 포함 여부를 “고정 EC2 4대 시나리오” vs “Batch 전용”에 따라 선택 가능하거나 문서로 명확히 구분.

---

## 7. 참고 파일 위치

| 구분 | 경로 |
|------|------|
| Batch 워커 엔트리 | `apps/worker/video_worker/batch_main.py`, `batch_entrypoint.py` |
| Batch 제출 | `apps/support/video/services/batch_submit.py`, `video_encoding.py` |
| Batch 인프라 스크립트 | `scripts/infra/batch_video_*.ps1`, `batch_ensure_ce_*.ps1`, `batch_update_ce_ami.ps1`, `batch_attach_ecs_instance_role_policies.ps1` |
| Lambda | `infra/worker_asg/queue_depth_lambda/lambda_function.py`, `infra/worker_autoscale_lambda/lambda_function.py` |
| Redis ASG 인터럽트 | `apps/support/video/redis_status_cache.py`, `apps/support/video/views/internal_views.py` (VideoAsgInterruptStatusView) |
| 계획/점검 문서 | `docs/VIDEO_BATCH_REFACTOR_PLAN_OF_RECORD.md`, `docs/DEPLOY_AND_ASG_REMNANT_CHECK.md`, `docs/VIDEO_BATCH_VERIFICATION_CHECKLIST.md` |

---

## 8. 검증 실행 결과 (SYSTEM HEALTH SUMMARY)

**실행:** `python manage.py validate_batch_video_system`  
**실행 일시:** 2026-02-22 (EC2 또는 DB+AWS 자격 증명 가능 환경)

### 8.1 검증 출력 (원문)

```
STEP 1 Latest jobs: [
  {
    "id": "84f00c1d-e16e-45ad-b39d-ab40aecaf84e",
    "state": "QUEUED",
    "aws_batch_job_id": "",
    "attempt_count": 1,
    "error_code": "",
    "error_message": "",
    "heartbeat": null,
    "updated_at": "2026-02-22T13:49:41.738102+00:00"
  },
  {
    "id": "74b44407-0db7-4218-ad24-0e36d254fc05",
    "state": "QUEUED",
    "aws_batch_job_id": "",
    "attempt_count": 1,
    "error_code": "",
    "error_message": "",
    "heartbeat": null,
    "updated_at": "2026-02-22T13:49:39.375789+00:00"
  },
  {
    "id": "0e268d41-9932-404e-ba10-f53b396de983",
    "state": "QUEUED",
    "aws_batch_job_id": "",
    "attempt_count": 1,
    "error_code": "",
    "error_message": "",
    "heartbeat": null,
    "updated_at": "2026-02-22T13:49:37.119654+00:00"
  }
]
STEP 1 Validation rules failed for above jobs.
STEP 3 Logs: BATCH_PROCESS_START=False BATCH_JOB_COMPLETED=False
STEP 4 scan_stuck_video_jobs (dry-run): OK Done: recovered=0 dead=0 (dry-run)
STEP 5 queue_depth ENABLE_VIDEO_METRICS: (missing=ok)
STEP 5 autoscale ENABLE_VIDEO_WAKE: (missing=ok)
STEP 6 academy-video-batch-job-role: False academy-batch-ecs-task-execution-role: True
STEP 6 IAM error:

============================================================
DB_CHECK: FAIL
BATCH_CHECK: OK
LOG_CHECK: FAIL
STUCK_CHECK: OK
LAMBDA_CHECK: OK
IAM_CHECK: FAIL
SYSTEM_STATUS: NEEDS_ATTENTION
============================================================
```

### 8.2 결과 요약

| Check | Result | 비고 |
|-------|--------|------|
| DB_CHECK | FAIL | 최근 3건이 `state=QUEUED`, `aws_batch_job_id=""` → 규칙(aws_batch_job_id 비어 있으면 안 됨) 미충족. submit 실패 또는 마이그레이션/제출 경로 점검 필요 |
| BATCH_CHECK | OK | 조회된 job에 aws_batch_job_id 없어 describe-jobs 미실행. 실패로 판정된 항목 없음 |
| LOG_CHECK | FAIL | BATCH_PROCESS_START / BATCH_JOB_COMPLETED 미확인(로그 스트림 또는 성공 job 없음) |
| STUCK_CHECK | OK | `scan_stuck_video_jobs --dry-run` 정상 완료 |
| LAMBDA_CHECK | OK | queue_depth ENABLE_VIDEO_METRICS, autoscale ENABLE_VIDEO_WAKE 없음 → missing=ok (Video 비활성) |
| IAM_CHECK | FAIL | academy-video-batch-job-role 없음 또는 조회 실패. academy-batch-ecs-task-execution-role 존재 |

**SYSTEM_STATUS:** NEEDS_ATTENTION

### 8.3 권장 후속 조치

- **DB_CHECK:** Batch 제출 성공 시 `aws_batch_job_id` 저장 여부 확인. QUEUED 상태 job에 id가 비어 있다면 submit 경로 또는 Batch 권한/설정 점검.
- **IAM_CHECK:** Job Definition에서 사용하는 `academy-video-batch-job-role` 생성 및 정책 부착 후 `batch_video_verify_and_register.ps1` 등으로 JobDef 재등록.
- **LOG_CHECK:** 한 건이라도 Batch에서 SUCCEEDED로 완료된 후 동일 run에서 CloudWatch 로그 스트림에 BATCH_PROCESS_START·BATCH_JOB_COMPLETED가 있는지 확인.

---

이 보고서는 리포지터리 내 코드·문서를 기준으로 작성되었으며, 실제 AWS 계정의 리소스(ASG 존재 여부, Lambda 배포 버전 등)는 별도 확인이 필요합니다.
