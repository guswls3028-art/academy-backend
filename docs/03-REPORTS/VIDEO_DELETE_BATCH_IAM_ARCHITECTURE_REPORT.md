# Video Delete · AWS Batch · Worker · IAM 아키텍처 기술 보고서

**작성 목적:** Video 삭제 시 Batch Terminate, Worker 동작, IAM 권한 분리 및 실패 모드 진단  
**범위:** API 서버, AWS Batch, Worker 컨테이너, 인프라 스크립트(PS1/JSON), DB 모델

---

## 1. Executive Summary (1페이지)

### 핵심 요약

- **Video 삭제 흐름:** 사용자 DELETE 요청 → API `VideoViewSet.perform_destroy()` → 진행 중 Job이 있으면 `batch_control.terminate_batch_job(aws_batch_job_id, "video_deleted")` 호출 → **동일 요청 내** Job 상태를 DEAD로 저장 → Video 레코드 삭제 → R2 정리는 SQS(`enqueue_delete_r2`)로 비동기 위임. Terminate 실패해도 삭제 요청은 성공 처리(best-effort).
- **Batch 연동:** API 서버와 Reconcile 커맨드가 `boto3.client("batch", region_name=...)`로 SubmitJob / TerminateJob / DescribeJobs / ListJobs 호출. **클라이언트 생성은 전부 기본 credential chain**(환경 변수 또는 EC2/Batch task role), 별도 STS assume 또는 custom session 없음. 리전은 `settings.AWS_REGION` / `AWS_DEFAULT_REGION` 또는 기본값 `ap-northeast-2`.
- **Worker:** AWS Batch Job으로 실행되며 **Batch API를 호출하지 않음.** SSM에서 env 로드 → DB에서 Job 조회 → `job_set_running` / `job_complete` / `job_fail_retry` 등 DB·Redis·R2만 사용. 삭제된 영상은 `_video_still_exists()`로 감지 후 `job_complete` 없이 exit(0).
- **IAM 불일치(핵심 리스크):**
  - **API 서버**는 EC2 Instance Profile `academy-ec2-role` 사용. `scripts/apply_api_batch_submit_policy.ps1`이 적용하는 정책은 `infra/worker_asg/iam_policy_api_batch_submit.json` **한 개뿐**이며, 이 파일에는 **`batch:SubmitJob`만** 있고 **`batch:TerminateJob`이 없음.**  
  → **영상 삭제 시 API에서 호출하는 `terminate_job`가 AccessDenied로 실패할 수 있음.** (실제 계정에서 별도 정책을 붙였을 가능성은 있으나, 저장소 기준으로는 누락.)
  - **Reconcile**은 Batch Job으로 돌 때 **task role** `academy-video-batch-job-role` 사용. 이 역할에는 `scripts/infra/iam/policy_video_job_role.json`(SSM, ECR, logs, CloudWatch)과 `AcademyAllowBatchDescribeJobs`(DescribeJobs, ListJobs)만 명시되어 있고, **`batch:TerminateJob`은 정의되어 있지 않음.**  
  → Reconcile의 orphan/duplicate `terminate_job` 호출이 동일 계정에서 수동으로 부여한 권한이 없으면 AccessDenied 가능.
- **Worker는 Batch API를 쓰지 않으므로** Batch 권한과 무관하게 동작하며, Terminate 실패 원인은 “Worker가 안 돈다”가 아니라 **API(또는 Reconcile) 측 IAM 권한 부족**으로 보는 것이 맞음.

### 권장 즉시 조치

1. **API용 역할(`academy-ec2-role`)에 `batch:TerminateJob` 추가**  
   - 리소스는 기존과 동일하게 job-definition / job-queue ARN으로 제한 가능.  
   - `iam_policy_api_batch_submit.json`(및 `.min.json`)에 Action에 `batch:TerminateJob` 추가 후 `apply_api_batch_submit_policy.ps1` 재실행.
2. **Reconcile용 역할(`academy-video-batch-job-role`)에 `batch:TerminateJob` 추가**  
   - `AcademyAllowBatchDescribeJobs` 확장 또는 별도 정책으로 `batch:TerminateJob` Resource `*` 허용.  
   - `iam_attach_batch_describe_jobs.ps1`과 분리해 두어도 됨(역할이 같음).
3. **운영 확인:**  
   - Video 삭제 후 API/CloudWatch 로그에서 `VIDEO_DELETE_TERMINATE_OK` / `VIDEO_DELETE_TERMINATE_FAILED` 및 DB `VideoOpsEvent` 타입 `VIDEO_DELETE_TERMINATE_REQUESTED` / `VIDEO_DELETE_TERMINATE_FAILED`로 Terminate 성공·실패 추적.

---

## 2. Architecture Breakdown

### 2.1 시스템 컴포넌트 식별

| 구분 | 위치 | 설명 |
|------|------|------|
| **API 진입점** | `apps/support/video/views/video_views.py` | `VideoViewSet.perform_destroy(instance)` — 영상 삭제 시 호출. |
| **삭제·Terminate 서비스** | `apps/support/video/services/batch_control.py` | `terminate_batch_job(aws_batch_job_id, reason, ...)` — Video 삭제용 Batch 즉시 종료. |
| **Submit/Terminate(Job ID 기준)** | `apps/support/video/services/batch_submit.py` | `submit_batch_job(video_job_id)`, `terminate_batch_job(video_job_id, reason)` — Reconcile/retry 등에서 사용. |
| **Batch 클라이언트 생성** | `batch_control.py` L51–52, `batch_submit.py` L54·105, `reconcile_batch_video_jobs.py` L105·363 | `boto3.client("batch", region_name=region)` — 매 호출 시 생성, 공통 래퍼 없음. |
| **Worker 엔트리** | `apps/worker/video_worker/batch_entrypoint.py` → `batch_main.py` | SSM `/academy/workers/env` 로드 후 `python -m apps.worker.video_worker.batch_main` 실행. |
| **Worker 완료/실패** | `academy/adapters/db/django/repositories_video.py` | `job_complete()`, `job_fail_retry()`, `job_mark_dead()` — DB/Redis 갱신. |
| **VideoOpsEvent 생성** | `apps/support/video/services/ops_events.py` | `emit_ops_event(type, ...)` → `VideoOpsEvent.objects.create(...)`. |
| **DB 모델** | `apps/support/video/models.py` | `Video`, `VideoTranscodeJob`, `VideoOpsEvent` (타입에 `VIDEO_DELETE_TERMINATE_REQUESTED` / `VIDEO_DELETE_TERMINATE_FAILED` 포함). |

### 2.2 Video 삭제 → Batch Terminate 제어 흐름 (단계별)

1. **DELETE** `/api/.../videos/{id}/` → `VideoViewSet.perform_destroy(instance)`  
   - `video_views.py` L160–208.
2. **진행 중 Job 조회**  
   - `video.current_job_id`로 `VideoTranscodeJob` 조회, `state in (QUEUED, RUNNING, RETRY_WAIT)`이고 `aws_batch_job_id`가 있으면:
3. **Terminate 호출**  
   - `batch_control.terminate_batch_job(aws_batch_job_id, "video_deleted", video_id=..., job_id=...)`  
   - 내부: `emit_ops_event("VIDEO_DELETE_TERMINATE_REQUESTED", ...)` → `boto3.client("batch").terminate_job(jobId=..., reason=...)`  
   - 성공 시 로그 `VIDEO_DELETE_TERMINATE_OK`, 실패 시 로그 `VIDEO_DELETE_TERMINATE_FAILED` + `emit_ops_event("VIDEO_DELETE_TERMINATE_FAILED", ...)`.  
   - **예외를 호출자에게 전파하지 않음** → 삭제는 계속 진행.
4. **DB 반영**  
   - 해당 Job `state=DEAD`, `save(update_fields=["state", "updated_at"])`.  
   - 그 다음 `super().perform_destroy(instance)`로 Video 삭제, 이후 `enqueue_delete_r2(...)`로 R2 삭제 메시지 전송.

### 2.3 AWS Batch 연동 정리

- **Batch 클라이언트 생성 위치**
  - `apps/support/video/services/batch_control.py` L51–52  
    `client = boto3.client("batch", region_name=region)`  
  - `apps/support/video/services/batch_submit.py` L54, L105  
    동일 패턴.  
  - `apps/support/video/management/commands/reconcile_batch_video_jobs.py` L105 (`_describe_jobs_boto3`), L363 (orphan 구간)  
    `client = boto3.client("batch", region_name=REGION)`.
- **자격 증명**  
  - **별도 설정 없음.** boto3 기본 체인(환경 변수 `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY` 또는 EC2/Batch task role) 사용.  
  - STS assume role, custom session 생성 코드 없음.
- **리전**  
  - `batch_control._batch_region()`: `settings.AWS_REGION` → `AWS_DEFAULT_REGION` → `"ap-northeast-2"`.  
  - `batch_submit`, reconcile: `getattr(settings, "AWS_REGION", None) or getattr(settings, "AWS_DEFAULT_REGION", "ap-northeast-2")` 등으로 동일하게 fallback.  
  - 하드코딩된 기본값만 사용, 리전을 다른 소스에서 주입하는 코드 없음.

### 2.4 API 서버 vs Worker 사용 주체

| 주체 | 용도 | 호출 API | 사용 역할(설정 기준) |
|------|------|----------|----------------------|
| **API 서버** (EC2) | 업로드 완료 시 Job 제출, 영상 삭제 시 Terminate | SubmitJob, **TerminateJob** | EC2 Instance Profile **academy-ec2-role** |
| **Reconcile** (Batch Job) | DescribeJobs, ListJobs, orphan/duplicate 시 Terminate | DescribeJobs, ListJobs, **TerminateJob**, SubmitJob(resubmit) | Batch Job **jobRoleArn** = **academy-video-batch-job-role** |
| **Worker** (Batch Job) | DB/Redis/R2만 사용 | 없음 | **academy-video-batch-job-role** (Batch API 불필요) |

---

## 3. IAM & Infrastructure Analysis

### 3.1 검색 대상 및 결과 요약

- **Terraform:** 없음 (저장소에 `*.tf` 없음).
- **인프라 정의:** `scripts/infra/*.ps1`, `scripts/infra/iam/*.json`, `scripts/infra/batch/*.json`, `infra/worker_asg/*.json` 등.
- **API 서버 역할:** `scripts/apply_api_batch_submit_policy.ps1`에서 **academy-ec2-role**에 인라인 정책 `BatchSubmitVideoJob` 적용.  
  - 정책 문서: `infra/worker_asg/iam_policy_api_batch_submit.json`  
  - 내용: **`batch:SubmitJob`만** 허용, Resource는 job-definition / job-queue ARN으로 제한.  
  - **`batch:TerminateJob` 없음** → 영상 삭제 시 API의 terminate 실패 가능.
- **Worker / Reconcile 역할:**  
  - Batch Job Definition의 **jobRoleArn** = **academy-video-batch-job-role**  
  - 역할 정책: `scripts/infra/iam/policy_video_job_role.json`  
    - SSM GetParameter, ECR, CloudWatch Logs, CloudWatch PutMetricData.  
    - **Batch 관련 Action 없음.**  
  - Reconcile용 추가 권한: `scripts/infra/iam_attach_batch_describe_jobs.ps1`로 **AcademyAllowBatchDescribeJobs** 부착  
    - `batch:DescribeJobs`, `batch:ListJobs`, Resource `*`.  
    - **`batch:TerminateJob` 없음** → Reconcile의 terminate(orphan/duplicate) 실패 가능.
- **EventBridge → Batch Submit:**  
  - `scripts/infra/iam/policy_eventbridge_batch_submit.json`: SubmitJob, DescribeJobs, DescribeJobQueues, DescribeJobDefinitions, iam:PassRole.  
  - EventBridge는 Terminate 호출하지 않음.

### 3.2 비교표

| Component | IAM Role | Permissions (저장소 기준) | Risk Level | Notes |
|-----------|----------|--------------------------|------------|-------|
| **API server** (video delete, submit) | academy-ec2-role | batch:SubmitJob (job-definition, job-queue ARN만) | **HIGH** | **batch:TerminateJob 없음** → Video 삭제 Terminate 실패 가능 |
| **API server** (일반 요청) | academy-ec2-role | 기타 EC2/앱 정책 | — | 삭제 외 경로는 동일 역할 |
| **Worker** (Batch container) | academy-video-batch-job-role | SSM, ECR, logs, CloudWatch | LOW | Batch API 미호출, 정상 동작 |
| **Reconcile** (Batch container) | academy-video-batch-job-role | 위 + DescribeJobs, ListJobs (AcademyAllowBatchDescribeJobs) | **MEDIUM** | **batch:TerminateJob 없음** → orphan/duplicate terminate 실패 가능 |
| **EventBridge** (submit only) | EventBridge용 역할 | SubmitJob, Describe*, PassRole | LOW | Terminate 미사용 |
| **Batch CE/Queue** | academy-batch-service-role, academy-batch-ecs-instance-profile | policy_batch_service_role.json, ECS 정책 | LOW | 인프라 관리용 |

### 3.3 리소스 제한

- **API용 정책** (`iam_policy_api_batch_submit.json`): Resource가 job-definition / job-queue ARN으로 제한됨.  
- **AcademyAllowBatchDescribeJobs**: `"Resource":"*"` — Describe/List만 있으므로 범위는 넓지만 동작상 필요 최소 권한에 가깝게 유지 가능.  
- **Worker 역할**: Batch 리소스 접근 없음.

---

## 4. Worker Lifecycle & VIDEO_DELETE_TERMINATE

### 4.1 Worker 취소 감지 및 삭제와의 연동

- **취소 플래그:**  
  - `job_is_cancel_requested(job_id)` + `_shutdown_event.is_set()` + **`_video_still_exists(job_obj.video_id)`**  
  - `batch_main.py` L166 `_cancel_check` 람다에 위 조건 포함.  
  - `_video_still_exists`는 `Video.objects.filter(pk=video_id).exists()`로, **영상 삭제 시 False**가 되어 Worker가 “삭제로 인한 취소”로 간주.
- **동작 요약:**  
  - 시작 직후 `job_set_running` 전에 `_video_still_exists`가 False면 → `WORKER_CANCELLED_BY_VIDEO_DELETE` 로그 후 exit(0), `job_complete` 미호출.  
  - `process_video` 완료 직후, `job_complete` 호출 전에 `_video_still_exists`가 False면 → 동일 로그 후 exit(0).  
  - `CancelledError` 발생 시에도 `_video_still_exists`가 False면 exit(0), True면 `job_fail_retry(job_id, "CANCELLED")`.
- **SIGTERM:**  
  - `_handle_term`에서 `job_fail_retry(jid, "TERMINATED")` 호출 후 `sys.exit(1)`.  
  - API에서 TerminateJob을 호출하면 Batch가 컨테이너에 SIGTERM을 보내므로, 위 핸들러가 실행되어 DB에 RETRY_WAIT(또는 이후 DEAD)로 반영됨.

### 4.2 Terminate 실패 시

- **API:**  
  - `batch_control.terminate_batch_job` 내부에서 예외를 잡아 로그 + `VIDEO_DELETE_TERMINATE_FAILED` 이벤트만 남기고, 호출부에는 예외를 전파하지 않음.  
  - 따라서 **Terminate가 AccessDenied 등으로 실패해도** DB에서는 Job이 DEAD로 마킹되고 Video는 삭제됨.  
  - Batch Job은 그대로 RUNNING으로 남을 수 있음 → Reconcile의 orphan 로직에서 나중에 terminate 시도(이때 Reconcile 역할에 TerminateJob 권한이 있어야 함).

### 4.3 레이스 가능성

- **Job이 완료되는 동시에 삭제:**  
  - API가 먼저 Job을 DEAD로 만들고 Video를 삭제한 뒤, Worker가 `job_complete`를 호출하면 `job_not_found` 또는 `video_not_found`로 실패할 수 있음.  
  - `job_complete`는 (False, reason)을 반환하고, batch_main에서는 `RuntimeError`를 발생시켜 `BATCH_JOB_FAILED` 로그 + `job_fail_retry`로 처리.  
  - Video는 이미 삭제된 상태이므로 사용자 관점에서는 “삭제됨”으로 일관됨.
- **Terminate 실패 + DB는 DEAD:**  
  - 위와 같이 API는 Terminate 실패를 삼키고 DEAD + 삭제까지 진행.  
  - Batch 쪽 Job은 계속 실행되다가 나중에 Worker가 `_video_still_exists`로 삭제를 감지하면 exit(0)하거나, Reconcile이 orphan으로 terminate 시도.
- **Worker가 완료한 뒤 삭제:**  
  - 이미 SUCCEEDED/READY이면 API의 perform_destroy에서 `state in (QUEUED, RUNNING, RETRY_WAIT)`에 해당하지 않아 Terminate를 호출하지 않음.  
  - 삭제만 진행되고, Batch Job은 이미 SUCCEEDED 상태.

---

## 5. Failure Mode Simulation

코드 기준으로 각 시나리오에서의 로그·이벤트·DB 상태를 정리한 것임.

| # | 시나리오 | 로그 | 이벤트 | DB 최종 상태 |
|---|----------|------|--------|--------------|
| 1 | **TerminateJob AccessDenied** (API) | `VIDEO_DELETE_TERMINATE_FAILED \| ... error=...` | VIDEO_DELETE_TERMINATE_REQUESTED 1건, VIDEO_DELETE_TERMINATE_FAILED 1건 | Job=DEAD, Video 삭제됨. Batch Job은 RUNNING 유지 가능. |
| 2 | **네트워크 타임아웃** (API, Batch 호출 시) | 동일하게 VIDEO_DELETE_TERMINATE_FAILED, exception 로그 | 위와 동일 | 동일. Terminate는 best-effort. |
| 3 | **Job이 이미 SUCCEEDED** | Terminate 호출 안 함 (state 조건 불일치) | VIDEO_DELETE_TERMINATE_REQUESTED 없음 | Job=SUCCEEDED 유지 후 Video 삭제. |
| 4 | **Worker가 SIGTERM 수신** | `BATCH_TERMINATED` JSON 로그, `job_fail_retry` 호출 | (별도 이벤트 없음, JOB_DEAD 등은 attempt 초과 시) | Job=RETRY_WAIT(또는 이후 DEAD). |
| 5 | **DB commit 실패 (job_complete 직후)** | Worker: `BATCH_JOB_FAILED` + exception. API는 이미 삭제 완료 가능. | — | 트랜잭션 롤백으로 Job/Video 갱신이 안 될 수 있음. Reconcile이 Batch 상태로 나중에 동기화. |

---

## 6. Architecture Diagram (Text)

```
┌─────────┐     DELETE /videos/{id}/      ┌──────────────────────────────────────────────────────────┐
│  User   │ ──────────────────────────────►│  API Server (EC2)                                        │
└─────────┘                               │  Role: academy-ec2-role                                  │
                                          │  Credentials: EC2 Instance Profile (default chain)       │
                                          │  • batch_control.terminate_batch_job() → boto3 batch     │
                                          │  • batch_submit.submit_batch_job() (upload_complete 등)   │
                                          └───────────────┬──────────────────────────┬───────────────┘
                                                          │                         │
                    ┌─────────────────────────────────────┘                         └─────────────────────┐
                    │                                                                                      │
                    ▼                                                                                      ▼
         ┌─────────────────────┐                                                              ┌─────────────────────┐
         │  RDS (DB)           │                                                              │  AWS Batch           │
         │  Video,             │◄────────────────────────────────────────────────────────────│  SubmitJob           │
         │  VideoTranscodeJob, │   Job 상태 갱신 (job_complete, job_fail_retry, DEAD 등)       │  TerminateJob        │
         │  VideoOpsEvent      │                                                              │  DescribeJobs/List   │
         └─────────────────────┘                                                              └──────────┬──────────┘
                    ▲                                                                                     │
                    │                                                                                     │ ECS Task
                    │         job_get_by_id, job_set_running, job_complete,                              │ Role:
                    │         job_fail_retry, job_heartbeat, _video_still_exists                          │ academy-video-
                    │                                                                                     │ batch-job-role
         ┌─────────┴─────────────────────────────────────────────────────────────────────────┐          │ (SSM, ECR, logs,
         │  Worker (Batch Container)                                                          │          │  CloudWatch only;
         │  Entry: batch_entrypoint → batch_main                                              │◄─────────┘  no Batch API)
         │  Env: SSM /academy/workers/env                                                     │
         │  • No boto3 batch calls                                                           │
         └───────────────┬───────────────────────────────────────────────────────────────────┘
                         │
                         │  R2 upload (HLS), delete_object_r2_video
                         ▼
         ┌─────────────────────┐         ┌─────────────────────────────────────────────┐
         │  R2 (S3-compat)      │         │  Reconcile (Batch Job, academy-video-ops-*)  │
         │  VIDEO_BUCKET       │         │  Role: academy-video-batch-job-role         │
         └─────────────────────┘         │  • describe_jobs, list_jobs, terminate_job  │
                                         │  • Credentials: Batch task role             │
                                         └─────────────────────────────────────────────┘

IAM boundaries:
  - API: academy-ec2-role (needs batch:SubmitJob + batch:TerminateJob; TerminateJob missing in repo).
  - Worker: academy-video-batch-job-role (no Batch API; DB/Redis/R2/SSM/ECR/logs).
  - Reconcile: same role; needs DescribeJobs, ListJobs, TerminateJob (TerminateJob not in repo).
Trust: EC2 → default credential chain; Batch task → jobRoleArn.
```

---

## 7. Risk & Improvement Report

### 7.1 리스크 분류

| 구분 | 내용 | 심각도 |
|------|------|--------|
| **API에 batch:TerminateJob 없음** | `iam_policy_api_batch_submit.json`에 TerminateJob 미포함 → 영상 삭제 시 Terminate 실패 가능 | **CRITICAL** |
| **Reconcile 역할에 batch:TerminateJob 없음** | policy_video_job_role + AcademyAllowBatchDescribeJobs만으로는 orphan/duplicate terminate 불가 | **HIGH** |
| **Resource "*"** | AcademyAllowBatchDescribeJobs가 `"Resource":"*"` — Describe/List만 있으므로 중간 수준. 필요 시 job-queue/job-definition으로 제한 가능 | **MEDIUM** |
| **Terminate 재시도 없음** | batch_control에서 실패 시 재시도 로직 없음. 네트워크 일시 오류 시 실패로 남음 | **MEDIUM** |
| **Idempotency** | TerminateJob은 AWS 쪽에서 이미 SUCCEEDED 등이면 적절히 처리. 호출부는 best-effort라 멱등성 이슈는 낮음 | **LOW** |
| **Dead-letter** | Terminate 실패는 로그/VideoOpsEvent로만 남음. 별도 DLQ/재처리 없음 | **MEDIUM** |
| **구조화 로그** | batch_control은 포맷 문자열 로그, batch_main은 JSON. 통일 스키마 없음 | **LOW** |
| **권한 상승** | Worker 역할이 Batch API를 호출하지 않으므로, 현재 설계상 권한 상승 경로는 없음 | **LOW** |

### 7.2 권장 수정 사항 (실행 가능)

1. **API 역할에 batch:TerminateJob 추가**  
   - 파일: `infra/worker_asg/iam_policy_api_batch_submit.json` (및 `.min.json`).  
   - Action에 `"batch:TerminateJob"` 추가. Resource는 기존과 동일하게 job-definition, job-queue ARN 유지.  
   - 적용: `.\scripts\apply_api_batch_submit_policy.ps1` 재실행.
2. **Reconcile(academy-video-batch-job-role)에 batch:TerminateJob 추가**  
   - 방법 A: `AcademyAllowBatchDescribeJobs` 정책 문서에 Action에 `batch:TerminateJob` 추가 후 동일 스크립트로 유지.  
   - 방법 B: 새 Managed Policy(예: AcademyAllowBatchTerminateJob) 생성 후 동일 역할에 부착.  
   - Resource는 `*` 또는 job-queue/job ARN으로 제한 가능.
3. **batch_control 재시도(선택)**  
   - terminate_job 실패 시 botocore 예외 종류별로 1~2회 재시도(지수 백오프).  
   - 재시도 후에도 실패 시에만 VIDEO_DELETE_TERMINATE_FAILED 기록.
4. **운영 체크리스트**  
   - 배포 후 Video 삭제 1건 수행 → API 로그 `VIDEO_DELETE_TERMINATE_OK` 또는 `VIDEO_DELETE_TERMINATE_FAILED` 확인.  
   - `VideoOpsEvent`에서 `VIDEO_DELETE_TERMINATE_REQUESTED` / `VIDEO_DELETE_TERMINATE_FAILED` 조회.  
   - Reconcile 실행 시 CloudWatch에서 DescribeJobs/ListJobs/TerminateJob 관련 AccessDenied 없음 확인.

### 7.3 프로덕션용 빠른 체크리스트

- [ ] API 서버 역할(`academy-ec2-role`)에 `batch:TerminateJob` 부여 여부 확인(콘솔 또는 CLI).
- [ ] Reconcile용 역할(`academy-video-batch-job-role`)에 `batch:TerminateJob` 부여 여부 확인.
- [ ] 영상 삭제 후 API 로그에 `VIDEO_DELETE_TERMINATE_OK` 또는 `VIDEO_DELETE_TERMINATE_FAILED` 수집.
- [ ] 동일 삭제에 대해 DB `video_videoopsevent`에 `VIDEO_DELETE_TERMINATE_REQUESTED` 존재 여부 확인.
- [ ] Reconcile 실행 시 AccessDenied 없음 확인(CloudWatch Logs / academy-video-ops).
- [ ] Worker 로그에서 삭제된 영상에 대해 `WORKER_CANCELLED_BY_VIDEO_DELETE` 발생 여부 확인(선택).

---

## 8. 참조: 코드/설정 위치

| 항목 | 경로 |
|------|------|
| Video 삭제 + Terminate 호출 | `apps/support/video/views/video_views.py` L160–208 |
| Terminate (삭제용) | `apps/support/video/services/batch_control.py` |
| Submit/Terminate (job_id 기준) | `apps/support/video/services/batch_submit.py` |
| Reconcile describe/terminate | `apps/support/video/management/commands/reconcile_batch_video_jobs.py` |
| Worker 엔트리·메인 | `apps/worker/video_worker/batch_entrypoint.py`, `batch_main.py` |
| job_complete / job_fail_retry | `academy/adapters/db/django/repositories_video.py` |
| VideoOpsEvent 모델 | `apps/support/video/models.py` (VideoOpsEvent) |
| API Batch 정책 적용 | `scripts/apply_api_batch_submit_policy.ps1`, `infra/worker_asg/iam_policy_api_batch_submit.json` |
| Worker/Reconcile 역할 정책 | `scripts/infra/iam/policy_video_job_role.json`, `scripts/infra/iam_attach_batch_describe_jobs.ps1` |
| Batch Job Definition | `scripts/infra/batch/video_job_definition.json` |
| 검증 가이드 | `docs/video_delete_terminate_verification.md` |

---

**보고서 끝.**
