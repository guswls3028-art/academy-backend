# Worker 아키텍처 사실 확인 리포트

**목적**: 이상향 정비 전 현황 파악 (코드 수정 없음)  
**작성일**: 2026-02-13

---

## A. Worker가 표현 계층을 참조하는지

### A.1 apps/worker/** 직접 import

**결과**: `apps.api`, `rest_framework`, `views`, `serializers`, `django.urls` 등을 **직접** import하는 코드는 **없음**.

- apps/worker 내 `.py` 파일에서 `apps.api`, `rest_framework`, `views`, `serializers`, `django.urls` 패턴 전수 조사 결과 0건
- README.md에 `DJANGO_SETTINGS_MODULE=apps.api.config.settings.prod` 환경변수 예시 있음 (문서용)

### A.2 간접(transitive) import

Worker가 import하는 모듈이 다시 표현 계층/API를 끌어오는지 추적:

| Worker | 직접 import | 간접 import 체인 | 표현 계층 도달 여부 |
|--------|-------------|------------------|---------------------|
| **Video** | apps.support.video.services.sqs_queue | sqs_queue → apps.support.video.models.Video | Video → **apps.api.common.models.TimestampModel** |
| **AI** | apps.support.ai.services.sqs_queue | sqs_queue → apps.domains.ai.models.AIJobModel | AIJobModel → django.db.models (직접 apps.api 아님) |
| **Messaging** | apps.support.messaging.services, models, credit_services | NotificationLog, Tenant, credit_services | Tenant → apps.core.models (core는 views/serializers에 rest_framework 사용, models는 django만) |

**핵심**:
- **Video Worker**: `apps.support.video.models.Video` → `apps.api.common.models.TimestampModel` 상속
- **AI Worker**: `apps.domains.ai.models.AIJobModel` → `django.db.models` (apps.api 직접 참조 없음)
- **Messaging Worker**: `apps.core.models.Tenant` → `apps.api.common.models.TimestampModel` 상속 가능성 (core.models 구조 확인 필요)

**결론**: Worker 코드는 표현 계층(views/serializers/rest_framework)을 **직접** import하지 않으나, **간접적으로 `apps.api.common.models`에 의존**한다.  
Video/Support/Models가 `TimestampModel`(apps.api.common)을 상속하기 때문.

---

## B. Worker가 HTTP로 API를 호출하는지

### B.1 내부 API 호출 (제거 대상)

| 파일 | 라인 | 호출 내용 | 용도 |
|------|------|----------|------|
| **apps/worker/ai_worker/run.py** | 56 | `requests.get(API_BASE_URL/api/v1/internal/ai/job/next/)` | Job 조회 |
| **apps/worker/ai_worker/run.py** | 90 | `requests.post(API_BASE_URL/api/v1/internal/ai/job/result/)` | 결과 제출 |

- **run.py (AI)**: HTTP polling 방식 → 현재 Docker CMD는 `sqs_main_cpu`/`sqs_main_gpu` 사용 → **run.py는 SQS 전환 후 레거시**로 보임. 다만 코드 상으로는 여전히 API HTTP 호출 존재.
- **wrong_note_worker**: 제거 완료. 오답노트 PDF 생성은 AI CPU 워커로 통합됨.

### B.2 API 호출이 아닌 HTTP 사용 (유지)

| 파일 | 용도 |
|------|------|
| video_worker/sqs_main, ai_worker/sqs_main_*, run.py | EC2 IMDSv2 메타데이터 (169.254.169.254) — AWS 인프라 |
| video_worker/download.py | R2/S3 presigned URL 다운로드 |
| ai_worker/storage/downloader.py | presigned URL 다운로드 |
| ai_worker/ai/omr/template_meta.py | 외부 URL (템플릿 메타) |

---

## C. Worker가 Django ORM/모델에 의존하는지

### C.1 직접 의존

| Worker | 모듈 | 내용 |
|--------|------|------|
| **Messaging** | sqs_main.py | `NotificationLog.objects.create`, `get_tenant_messaging_info`, `deduct_credits`, `rollback_credits`, `is_reservation_cancelled` — **Django ORM 직접 사용** |
| **Video** | apps.support.video.services.sqs_queue | `Video` 모델, `transaction.atomic`, `Video.objects.filter().update()` |
| **AI** | apps.support.ai.services.sqs_queue | `AIJobModel`, `transaction.atomic`, `AIJobModel.objects.filter().update()` |

### C.2 완전 Django-less 가능 여부

- **Video/AI**: SQS 기반 큐 사용. 다만 `mark_processing`, `complete_video`, `fail_video`, `complete_job`, `fail_job` 등이 **DB 상태 업데이트**를 수행.
  - 큐는 SQS로 분리됐지만, **작업 상태·결과는 DB에 기록**하는 구조.
  - 따라서 **“큐 소비”는 SQS만으로 가능**하나, **상태 반영을 위해 Django ORM(또는 동등한 DB 접근)은 필요**.
- **Messaging**: 예약 취소 확인, 잔액 차감, NotificationLog 저장 등 **완전히 ORM에 의존**.
- **결론**:  
  - **SQS/Redis는 유지**,  
  - **DB 접근(상태 갱신, 로깅)은 Application/Infrastructure 레이어로 격리**하여, Worker 런타임은 “핸들러 호출”만 담당하는 형태로 리팩터링 가능.  
  - **완전 Django-less**는 현 구조에서는 **불가**. DB 상태 업데이트를 raw SQL/다른 ORM으로 옮기는 것은 대규모 변경이므로, **Django ORM은 Infrastructure 어댑터로 한정**하는 방향이 현실적.

---

## D. 실행 단위 정의

### D.1 실제 배포 시 사용되는 엔트리포인트

| 워커 | 엔트리포인트 | Docker CMD | 비고 |
|------|--------------|------------|------|
| **Video** | `apps.worker.video_worker.sqs_main` | `python -m apps.worker.video_worker.sqs_main` | SQS Long Polling |
| **AI CPU** | `apps.worker.ai_worker.sqs_main_cpu` | `python -m apps.worker.ai_worker.sqs_main_cpu` | SQS Lite+Basic |
| **AI GPU** | `apps.worker.ai_worker.sqs_main_gpu` | `python -m apps.worker.ai_worker.sqs_main_gpu` | SQS Premium |
| **Messaging** | `apps.worker.messaging_worker.sqs_main` | `python -m apps.worker.messaging_worker.sqs_main` | SQS |

### D.2 레거시/미사용 엔트리포인트

| 파일 | 용도 |
|------|------|
| `apps.worker.ai_worker.run.py` | HTTP polling (API `/api/v1/internal/ai/job/next/` 호출) — Docker에서 미사용, SQS 전환으로 레거시 |
| `apps.worker.ai_worker.sqs_main.py` | 통합 진입점? — Docker는 cpu/gpu 분리 사용 |
---

## 요약

| 구분 | 결과 |
|------|------|
| **A. 표현 계층 직접 참조** | 없음 |
| **A. 간접 참조** | apps.api.common.models.TimestampModel (Video, Tenant 등 모델 상속) |
| **B. HTTP API 호출** | ai_worker/run.py — 배포 미사용 (SQS 전환 완료). wrong_note_worker 제거됨 |
| **C. Django ORM 의존** | Video/AI/Messaging 모두 DB 상태·로깅용 ORM 사용. 완전 제거 불가, 레이어 분리로 격리 가능 |
| **D. 실행 단위** | video: sqs_main / ai: sqs_main_cpu, sqs_main_gpu / messaging: sqs_main |
