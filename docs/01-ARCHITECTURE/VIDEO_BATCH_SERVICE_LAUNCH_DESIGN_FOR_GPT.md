# Video Batch 서비스 런칭 — GPT용 완성 설계도

이 문서는 **바로 서비스 런칭**을 위해 필요한 코드·인프라·운영 결정을 한곳에 모아, GPT(또는 다른 AI)가 “물어볼 만한 것”을 미리 채워 넣은 **완성 설계도**이다.  
지시: **이 문서만 읽고도 구현·배포·체크리스트를 생성할 수 있어야 한다.**

---

## 1. 프로젝트·서비스 개요

| 항목 | 내용 |
|------|------|
| 서비스 | 학원/교육용 비디오 업로드 → HLS 트랜스코딩 → 재생 |
| 아키텍처 | Django API + AWS Batch(EC2) 단일 실행. SQS 미사용. |
| 워커 | 컨테이너 1개 = 영상 1개. `batch_main.py`가 엔트리포인트. |
| DB 모델 | `VideoTranscodeJob` (state, attempt_count, aws_batch_job_id, last_heartbeat_at 등). `Video`는 current_job FK 보유. |
| 멀티테넌트 | `VideoTranscodeJob.tenant_id`, Video→Session→Lecture→Tenant. API는 호스트 기반 테넌트 해석. |

---

## 2. 확정된 운영·비용 결정 (변경 금지)

| 항목 | 확정값 | 비고 |
|------|--------|------|
| Compute Environment | **ON_DEMAND** | SPOT/Mixed는 추후. 초기 런칭은 반드시 ON_DEMAND. |
| minvCpus | **0** | 상시 대기 없음. 작업 있을 때만 인스턴스 생성. |
| desiredvCpus | **0** | |
| maxvCpus | **16** (초기). 여유 있게는 32. | 동시 인코딩 수 ≈ maxvCpus / job당 vCPU. |
| 인스턴스 타입 | **c6g.large**, **c6g.xlarge** (필요 시 c6g.2xlarge 포함 가능) | Job Definition은 vcpus=2, memory=4096. |
| 출력 프로파일 | **720p + 480p** | |
| 평균 영상 길이 | **약 3시간** | |
| 인코딩 속도 | **실시간 대비 약 1.1배** (과거 t4g.medium 기준). c6g에서는 미측정. | 비용 산정 시 3시간 영상 ≈ 2.7~3시간 CPU 사용 가정. |
| 월 트래픽 가정 (초기) | **월 약 100~300개** 수준. 학원 특성상 **특정 시간대 몰림** 가능. | 동시 업로드 수에 따라 maxvCpus 조정. |
| SLA | **미정의**. 추후 정의 예정. | |
| 유실 정의 | 업로드된 영상에 대한 인코딩 작업이 DB/시스템에서 사라지거나, 자동 복구 없이 멈춘 상태. | |
| 자동 복구 | **있으면 좋음**. 서비스 투입 전 최소: RUNNING 반영 + SIGTERM 처리. | |

---

## 3. 비용 참고 (검증용, 변경 가능)

- **c6g.large**: 약 $0.085/시간. 3시간 영상 1개 ≈ $0.23. **월 100개 ≈ $23 + 부가비용 ≈ $27~30.**
- **c6g.xlarge**: 약 $0.17/시간. 3시간 영상 1개 ≈ $0.46. **월 100개 ≈ $46 + 부가비용 ≈ $53~55.**
- Spot은 초기 런칭에서 **사용하지 않음**. ON_DEMAND만 사용.

---

## 4. 서비스 투입 전 필수 코드 수정 (반드시 구현)

아래 3가지를 **반드시** 적용한 뒤 서비스 투입.

### 4.1 RUNNING 상태 반영

- **목적:** Batch 작업이 실제로 시작되면 DB에 `state=RUNNING`, `last_heartbeat_at=now` 반영. Stuck 스캐너가 동작하려면 필수.
- **위치:** `apps/worker/video_worker/batch_main.py`
- **동작:**  
  - `job_id` 확정 후, `process_video` 호출 **직전**에 `job_set_running(job_id)` 호출.  
  - 이미 `job_set_running`이 있는 곳: `academy/adapters/db/django/repositories_video.py` (함수명 `job_set_running`).  
- **제약:** `job_set_running`은 state가 QUEUED 또는 RETRY_WAIT일 때만 RUNNING으로 변경. SUCCEEDED 등이면 호출해도 무시됨. idempotent 호출 가능.

### 4.2 SIGTERM 핸들러 추가

- **목적:** 컨테이너가 SIGTERM(스팟 회수, 인스턴스 종료, 타임아웃 등)을 받으면 정상적으로 `job_fail_retry` 호출 후 종료. DB를 QUEUED 방치하지 않음.
- **위치:** `apps/worker/video_worker/batch_main.py`
- **동작:**  
  - `main()` 진입 후, `job_id`를 확정한 직후에 시그널 핸들러 등록.  
  - 핸들러에서: `job_fail_retry(job_id, "SIGTERM" 또는 "INTERRUPTED" 등 고정 문자열)` 호출 후 `sys.exit(1)` (또는 비0 종료).  
  - `job_id`는 핸들러가 클로저로 참조할 수 있게 전달.  
- **주의:** `signal.signal(signal.SIGTERM, handler)` 사용. SIGINT는 선택(동일 처리해도 됨). Windows 호환 필요 시 플랫폼 분기 가능.

### 4.3 Stuck 스캐너 동작 조건 (수정 아님, 확인용)

- **현재:** `scan_stuck_video_jobs`는 `state=RUNNING` 이고 `last_heartbeat_at < now - threshold` 인 Job만 선택.  
- **4.1 적용 후:** Batch job도 RUNNING이 되므로, 워커가 죽으면 스캐너가 감지해 RETRY_WAIT + `submit_batch_job`(management command 한정) 가능.  
- **Internal API:** `POST /api/v1/internal/video/scan-stuck/` 는 RETRY_WAIT 전환만 하고 `submit_batch_job` 호출하지 않음. 재제출은 management command `scan_stuck_video_jobs` 또는 사용자 retry API에 의존.

---

## 5. 핵심 파일 경로 (구현 시 참고)

| 용도 | 경로 |
|------|------|
| Batch 워커 엔트리 | `apps/worker/video_worker/batch_main.py` |
| Batch 엔트리포인트(SSM→실행) | `apps/worker/video_worker/batch_entrypoint.py` |
| job_set_running / job_fail_retry / job_complete | `academy/adapters/db/django/repositories_video.py` |
| Job 생성 + Batch 제출 | `apps/support/video/services/video_encoding.py` |
| Batch 제출만 | `apps/support/video/services/batch_submit.py` |
| Stuck 스캐너 (mgmt) | `apps/support/video/management/commands/scan_stuck_video_jobs.py` |
| Stuck 스캐너 (internal API) | `apps/support/video/views/internal_views.py` → `VideoScanStuckView` |
| Job 모델 | `apps/support/video/models.py` → `VideoTranscodeJob` |
| 설정(Queue/Job Definition 이름) | `apps/api/config/settings/base.py` (VIDEO_BATCH_*, LAMBDA_INTERNAL_API_KEY 등) |
| Compute Environment JSON | `scripts/infra/batch/video_compute_env.json` |
| Job Definition JSON | `scripts/infra/batch/video_job_definition.json` |

---

## 6. 인프라·설정 요약 (GPT가 “실제 값”을 채울 때 참고)

- **리전:** ap-northeast-2 (서울). 설정은 `AWS_REGION` / `AWS_DEFAULT_REGION`, Batch 스크립트에서도 동일.
- **Batch Queue 이름:** 환경변수 `VIDEO_BATCH_JOB_QUEUE` (기본값 `academy-video-batch-queue`).
- **Job Definition 이름:** 환경변수 `VIDEO_BATCH_JOB_DEFINITION` (기본값 `academy-video-batch-jobdef`).
- **Compute Environment 이름:** `academy-video-batch-ce` (스크립트/JSON 기준).
- **Job Definition 내용:**  
  - command: `python -m apps.worker.video_worker.batch_main` + job_id (Ref::job_id 또는 환경변수 VIDEO_JOB_ID).  
  - vcpus=2, memory=4096.  
  - retryStrategy.attempts=1. timeout attemptDurationSeconds=14400.  
  - 로그: awslogs, log group `/aws/batch/academy-video-worker`.
- **Compute Environment (목표):**  
  - type EC2, **ON_DEMAND** (SPOT 사용 안 함).  
  - minvCpus=0, desiredvCpus=0, maxvCpus=16 (또는 32).  
  - instanceTypes: c6g.large, c6g.xlarge (필요 시 c6g.2xlarge).  
  - allocationStrategy: BEST_FIT_PROGRESSIVE.  
- **Internal API:** `/api/v1/internal/*` 는 테넌트 bypass. `LAMBDA_INTERNAL_API_KEY`, `INTERNAL_API_ALLOW_IPS` 등으로 보호. scan-stuck, dlq-mark-dead 등은 외부 노출 금지.

---

## 7. GPT가 물어볼 만한 질문 — 사전 답변

### 7.1 테넌트·인증

- **테넌트 식별:** 호스트 기반만 사용. 헤더/쿼리로 테넌트 지정하는 방식 없음. Internal API는 테넌트 없음.
- **Video/Job 접근:** Staff API는 `get_object()`로 Video 조회 후 `video.current_job_id`로 Job 접근. Job 직접 조회 시에는 `job_get_by_id(job_id)`만 사용(tenant_id 필터 없음). 내부 API는 신뢰된 호출자만 사용한다고 가정.
- **Progress/Workbox:** `session__lecture__tenant_id=tenant.id` 로 필터. tenant는 request에서 해석.

### 7.2 Batch·환경 변수

- **job_id 전달:** Batch submit 시 `parameters={"job_id": str(video_job_id)}`, containerOverrides 환경변수 `VIDEO_JOB_ID`. 워커는 `os.environ.get("VIDEO_JOB_ID")` 또는 `sys.argv[1]` 사용.
- **워커 설정:** `apps.api.config.settings.worker` (DJANGO_SETTINGS_MODULE). DB/Redis 등은 SSM 파라미터 `/academy/workers/env` 등으로 주입 가능(batch_entrypoint.py).
- **VIDEO_JOB_MAX_ATTEMPTS:** 기본 5. 환경변수로 override 가능. attempt_count >= 이 값이면 `job_mark_dead()` 호출.

### 7.3 재시도·에러 처리

- **앱 예외:** `job_fail_retry(job_id, reason)` → state=RETRY_WAIT, attempt_count 증가. 5회 이상이면 `job_mark_dead`.
- **Batch submit 실패:** `video_encoding.create_job_and_submit_batch`에서 job.state=FAILED, error_code/error_message 저장. 사용자 재시도로만 복구.
- **컨테이너 킬/SIGTERM:** 현재는 DB 갱신 없음. **4.2 적용 후** 핸들러에서 `job_fail_retry` 호출.

### 7.4 배포·이미지

- **워커 이미지:** Dockerfile 경로 `docker/video-worker/Dockerfile`. ECR 푸시 후 Job Definition의 image URI만 교체하면 됨.
- **배포:** API 서버와 워커 이미지는 별도. 워커만 수정 시 Batch Job Definition revision 업데이트 또는 새 revision 등록 후 Queue가 사용할 job definition 지정.

### 7.5 모니터링·운영

- **로그:** CloudWatch Logs, log group `/aws/batch/academy-video-worker`. 스트림 접두사 `batch`.
- **검증 명령:** `python manage.py validate_batch_video_system` (DB, describe-jobs, 로그, scan_stuck dry-run, Lambda, IAM 등).
- **Stuck 스캐너 실행:** `python manage.py scan_stuck_video_jobs` (cron 권장). 또는 Lambda로 `POST /api/v1/internal/video/scan-stuck/` 호출(재제출은 안 함).

### 7.6 기타

- **영상 인코딩:** CPU 기반(ffmpeg). GPU 아님. vcpus=2, memory=4096으로 720p+480p 처리.
- **동시 실행 수:** maxvCpus=16, job당 2 vCPU면 이론상 동시 8 job. 몰림이 크면 maxvCpus 32로 상향.

---

## 8. 서비스 투입 직전 실행 순서 (체크리스트)

1. **코드:** 4.1 RUNNING 반영, 4.2 SIGTERM 핸들러 적용 후 배포.
2. **인프라:** Compute Environment가 ON_DEMAND인지 확인. minvCpus=0, desiredvCpus=0, maxvCpus=16(또는 32) 확인.
3. **Job Definition:** retryStrategy.attempts=1, timeout 14400, vcpus/memory 확인.
4. **환경 변수:** API 서버에 VIDEO_BATCH_JOB_QUEUE, VIDEO_BATCH_JOB_DEFINITION 설정.
5. **테스트:** 컨테이너 강제 종료(kill 또는 Batch에서 중단) 1회 수행 후, DB에서 해당 job이 RETRY_WAIT(또는 실패 사유)로 갱신되는지 확인.
6. **실제 업로드:** 소수(예: 5개) 영상 업로드 → 인코딩 완료·재생 확인.

---

## 9. GPT에게 요청할 때 넣을 한 줄 지시 예시

- “아래 문서(VIDEO_BATCH_SERVICE_LAUNCH_DESIGN_FOR_GPT.md)를 기준으로, **RUNNING 반영**과 **SIGTERM 핸들러**를 `batch_main.py`에 구현해줘. 기존 `job_set_running`, `job_fail_retry` import 및 호출 규칙을 유지해줘.”
- “같은 문서를 기준으로 **배포 체크리스트**와 **강제 종료 후 DB 확인 방법**을 단계별로 정리해줘.”
- “같은 문서의 인프라 요약을 바탕으로, **ON_DEMAND인 Compute Environment**를 AWS CLI로 생성/수정하는 명령 예시를 만들어줘.”

---

이 문서는 실제 프로젝트 구조·파일 경로·설정·확정된 운영 결정을 반영했다. GPT는 이 문서만으로 나머지 구현·스크립트·체크리스트를 보완할 수 있다.
