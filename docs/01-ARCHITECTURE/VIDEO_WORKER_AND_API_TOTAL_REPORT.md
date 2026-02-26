# Video 워커 및 API 서버 총괄 보고서

이 문서는 HakwonPlus 프로젝트의 **Video 워커(AWS Batch)** 와 **API 서버** 에 대한 코드·인프라 총괄 설명입니다.  
**Video 인프라 리소스 이름·스펙·성공 조건의 단일 기준(SSOT)은 [00-SSOT/RESOURCE-INVENTORY.md](../00-SSOT/RESOURCE-INVENTORY.md) 및 [00-SSOT/ONE-TAKE-DEPLOYMENT.md](../00-SSOT/ONE-TAKE-DEPLOYMENT.md) 이다.**  
(외부 채팅에서 사용된 리소스 이름과 실제 코드/인프라가 다를 수 있으므로, 여기서는 **저장소와 SSOT 기준**으로만 기술합니다.)

---

## 1. 리소스 이름 (SSOT v1.1 기준)

| 구분 | 이름 | 비고 |
|------|------|------|
| **Video CE** | `academy-video-batch-ce-final` | 단일 CE. c6g.large만. |
| **Video Job Queue** | `academy-video-batch-queue` | CE 1개만 연결. |
| **Worker Job Definition** | `academy-video-batch-jobdef` | **주의:** `academy-video-job-definition` 이 아님. |
| **Ops CE / Queue** | `academy-video-ops-ce`, `academy-video-ops-queue` | reconcile/scan_stuck/netprobe. |
| **Ops Job Definitions** | `academy-video-ops-reconcile`, `academy-video-ops-scanstuck`, `academy-video-ops-netprobe` | |
| **EventBridge (reconcile)** | `academy-reconcile-video-jobs` | rate(15 minutes) → Ops Queue. Redis lock 사용. |
| **EventBridge (scan-stuck)** | `academy-video-scan-stuck-rate` | rate(5 minutes) → Ops Queue. |

- **전체 스펙·성공 조건:** [00-SSOT/RESOURCE-INVENTORY.md](../00-SSOT/RESOURCE-INVENTORY.md)
- 최종 상태 파일: `docs/02-OPERATIONS/actual_state/batch_final_state.json`
- 실행 순서: [00-SSOT/RUNBOOK.md](../00-SSOT/RUNBOOK.md), [02-OPERATIONS/video_batch_production_runbook.md](../02-OPERATIONS/video_batch_production_runbook.md)

---

## 2. API 서버 — 영상 업로드 → Batch 제출 경로

### 2.1 설정 (Django)

- **파일:** `apps/api/config/settings/base.py`
- **환경 변수:**
  - `VIDEO_BATCH_JOB_QUEUE` — 기본값 `academy-video-batch-queue`
  - `VIDEO_BATCH_JOB_DEFINITION` — 기본값 `academy-video-batch-jobdef`
  - `VIDEO_TENANT_MAX_CONCURRENT`, `VIDEO_GLOBAL_MAX_CONCURRENT`, `VIDEO_MAX_JOBS_PER_VIDEO` (동시 제출 제한)

### 2.2 제출 흐름 (코드 경로)

1. **진입점:** `apps/support/video/views/video_views.py`  
   - 업로드 완료/재시도 등 여러 경로에서 `create_job_and_submit_batch(video)` 호출 (예: 431, 453, 474, 560행 근처).

2. **Job 생성 + 제출:** `apps/support/video/services/video_encoding.py`  
   - `create_job_and_submit_batch(video)`:
     - `video.status == UPLOADED` 여야 함.
     - 이미 활성 Job(QUEUED/RUNNING/RETRY_WAIT) 있으면 idempotent return.
     - tenant/global/per-video 동시 제한 체크 후 `VideoTranscodeJob` 생성, `video.current_job_id` 저장.
     - `submit_batch_job(str(job.id))` 호출.
     - 성공 시 `job.aws_batch_job_id` 저장. 실패 시 트랜잭션 롤백.

3. **Batch API 호출:** `apps/support/video/services/batch_submit.py`  
   - `submit_batch_job(video_job_id)`:
     - `settings.VIDEO_BATCH_JOB_QUEUE`, `VIDEO_BATCH_JOB_DEFINITION` 사용.
     - **Job Definition:** 이름만 전달 (`academy-video-batch-jobdef`) → **revision 미지정** → AWS가 **최신 ACTIVE revision** 사용.
     - `jobName=f"video-{video_job_id[:8]}"`, `containerOverrides.environment`: `VIDEO_JOB_ID=video_job_id`.

- **구조:** SQS 없음. DB(VideoTranscodeJob) SSOT, Batch만 사용.

---

## 3. Video 워커 (AWS Batch) — 런타임

### 3.1 Job Definition (워커)

- **스펙·이미지 정책:** [00-SSOT/RESOURCE-INVENTORY.md](../00-SSOT/RESOURCE-INVENTORY.md) § Video Batch (vcpus=2, memory=3072, retryStrategy=1, immutable tag).
- **파일:** `scripts/infra/batch/video_job_definition.json`
- **등록:** `scripts/infra/batch_video_setup.ps1` [5] 단계에서 위 JSON 치환 후 `register-job-definition` 호출.
- **현재 내용 요약:** vcpus=2, memory=3072 (SSOT v1.1). `resourceRequirements` 는 Batch 스케줄링용으로 VCPU/MEMORY 명시 권장.

### 3.2 resourceRequirements 이슈 (스케줄링)

- AWS Batch **EC2** Compute Environment는 스케줄링 시 **`containerProperties.vcpus`를 사용하지 않고**, **`containerProperties.resourceRequirements`** 의 `VCPU`/`MEMORY` 만 사용합니다.
- `resourceRequirements` 가 비어 있으면 Batch는 해당 Job의 CPU 요구량을 **0**으로 간주하고, 한 EC2 인스턴스에 여러 컨테이너를 **packing** 할 수 있습니다.
- 그 결과:
  - **EC2 대수 < RUNNING Job 수** (예: 4대에 5개 이상 Job).
  - ffmpeg가 CPU 집약 작업인데 한 인스턴스에 2~3개가 동시 실행되면 CPU 경합 → HLS 지연 → R2 업로드/Redis heartbeat 지연 → RUNNING stuck → RETRY 가능성 증가.

**조치:**  
- `video_job_definition.json` 에 `resourceRequirements` 로 VCPU/MEMORY 명시 (SSOT v1.1: 2 vCPU, 3072 MiB).  
- API는 이미 Job Definition **이름만** 쓰므로 revision 지정 없음 → 새 revision 등록만 하면 이후 제출분부터 최신 revision 사용.

### 3.3 컨테이너 부팅 경로 (모든 Batch Job 공통)

- **Entrypoint:** `apps/worker/video_worker/batch_entrypoint.py`
  - SSM Parameter `/academy/workers/env` (또는 `BATCH_SSM_ENV`) 에서 JSON 읽어 `os.environ` 설정.
  - 필수 키 검증 후 `DJANGO_SETTINGS_MODULE=apps.api.config.settings.worker` 확인.
  - 그 다음 인자로 전달된 명령 실행 (워커: `python -m apps.worker.video_worker.batch_main`, reconcile: `python manage.py reconcile_batch_video_jobs` 등).

- **워커 메인:** `apps/worker/video_worker/batch_main.py`
  - `VIDEO_JOB_ID` (또는 argv[1]) 로 Job UUID 수신.
  - DB에서 Job 조회 → `job_set_running` → heartbeat 스레드 시작 → `process_video` (ffmpeg HLS + R2 업로드 등) → 완료 시 `job_complete` / 실패 시 `job_fail_retry`.
  - SIGTERM/SIGINT 시 `job_fail_retry` 후 종료 (scan_stuck/인프라 종료 대응).

- **환경:** DB/R2/Redis/API_BASE_URL/INTERNAL_WORKER_TOKEN 등은 Job Definition의 environment가 아니라 **SSM JSON** 으로만 주입됨. Job Definition에는 `VIDEO_JOB_ID` 를 넣기 위한 containerOverrides만 API에서 사용.

---

## 4. Reconcile — 왜 RUNNING 이 6~7개인가

### 4.1 트리거

- **EventBridge 규칙:** `academy-reconcile-video-jobs`
- **스크립트:** `scripts/infra/eventbridge_deploy_video_scheduler.ps1`
- **스케줄:** `rate(15 minutes)` (SSOT v1.1). Redis lock `video:reconcile:lock` TTL 600초로 동시 실행 1개만.
- **타깃:** Ops Queue. Job Definition: `academy-video-ops-reconcile`.

### 4.2 원인 (과거 2분 주기 시)

- reconcile 커맨드(`reconcile_batch_video_jobs`)는 DB 스캔, `describe_jobs`, orphan terminate 등으로 **한 번에 2분 이상 걸릴 수 있음**.
- EventBridge가 이전 실행 완료 여부를 보지 않고 주기마다 SubmitJob만 하면 RUNNING이 쌓임.
- **현재:** rate(15 minutes) + reconcile 코드 내 Redis lock으로 동시 실행 1개로 제한 (SSOT v1.1).

### 4.3 정리

- **영상 5개 + reconcile 6~7개 = RUNNING 11개** 는, “영상 Job 5개 + reconcile이 중복 실행된 6~7개”로 설명됨.
- Reconcile 자체는 **단일 인스턴스**가 주기적으로 한 번씩 돌면 충분함.

### 4.4 개선 옵션 (참고)

**현행:** rate(15 minutes) + Redis lock (SSOT v1.1). 아래 옵션은 과거 2분 주기 시 참고용이다.

1. **스케줄 완화:** EventBridge 규칙을 `rate(5 minutes)` 등으로 변경해, 한 번 실행이 끝날 시간을 주기.
2. **Single-flight:**  
   - Lambda 등에서 “현재 큐에 RUNNING인 `academy-video-ops-reconcile` Job이 있으면 SubmitJob 스킵” 로직 추가.  
   - 또는 reconcile 커맨드 내부에서 Redis/DynamoDB 등으로 “이미 다른 인스턴스가 실행 중이면 즉시 exit” 처리.
3. **임시 조치:** 이미 쌓인 reconcile Job만 정리하려면 `aws batch terminate-job --job-id <id> --reason "cleanup"` 로 필요 시 개별 종료.

---

## 5. 인프라 스크립트 요약

| 목적 | 스크립트 | 비고 |
|------|----------|------|
| CE / Queue / Worker JD / Ops JD 등록 | `scripts/infra/batch_video_setup.ps1` | Worker JD는 `batch/video_job_definition.json` 사용. Ops JD는 스크립트 내 인라인 정의(vcpus=1, resourceRequirements=@()). |
| API VPC 기준 Batch 재생성 | `scripts/infra/recreate_batch_in_api_vpc.ps1` | batch_video_setup 호출 등. |
| EventBridge (reconcile + scan-stuck) | `scripts/infra/eventbridge_deploy_video_scheduler.ps1` | reconcile: rate(15 min), scan_stuck: rate(5 min). Ops Queue 타깃. |
| SSM Bootstrap (.env → SSM) | `scripts/infra/ssm_bootstrap_video_worker.ps1` | Batch 컨테이너는 SSM JSON만 사용. |
| 검증 | `scripts/infra/verify_eventbridge_wiring.ps1`, `production_done_check.ps1` 등 | Runbook 참고. |

---

## 6. 한 줄 요약

- **API:** 영상 업로드 완료 시 `create_job_and_submit_batch` → `submit_batch_job` → `academy-video-batch-jobdef` (이름만, 최신 revision) 로 `academy-video-batch-queue` 에 제출.
- **워커:** 동일 이미지 + `batch_entrypoint` → SSM → `batch_main`, ffmpeg HLS + R2 + heartbeat.  
- **리소스 이름·스펙:** Video 인프라 SSOT는 [00-SSOT/RESOURCE-INVENTORY.md](../00-SSOT/RESOURCE-INVENTORY.md). CE=`academy-video-batch-ce-final`, Queue=`academy-video-batch-queue`, JobDef=`academy-video-batch-jobdef`.
- **Reconcile:** rate(15 minutes) + Redis lock. 다중 RUNNING은 주기 완화 및 single-flight로 해소됨 (SSOT v1.1).
