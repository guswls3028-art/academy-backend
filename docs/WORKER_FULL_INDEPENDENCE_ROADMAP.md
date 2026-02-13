# 완전 Worker 독립형 로드맵

## 🎯 목표 정의: "완전 Worker 독립형"

### 완전 독립의 의미

| 구분 | 현재 | 목표 |
|------|------|------|
| `apps.*` (Django 앱) | Worker가 `apps.support`, `apps.worker` 등 import | **import 안 함** |
| Django ORM | Queue/Adapter 내부에서 직접 사용 | **직접 의존 안 함** |
| Worker 이미지 | Django, DRF, Admin, View 계층 포함 | **제거 가능** |

### Worker가 사용할 수 있는 것 (최종)

- **Port** (인터페이스)
- **Adapter** (Infrastructure 구현체)
- **Domain** (순수 비즈니스 로직)
- **Redis / SQS** (외부 연동)

### Worker가 사용하면 안 되는 것 (최종)

- `apps.*`
- Django ORM (`Model.objects`)
- Django settings / `manage.py`
- DRF, Admin, Views, Serializers

---

## 🧭 단계별 로드맵

### 0단계: 현재 상태 ✅

**구조**

```
Worker → src.infrastructure 어댑터 (VideoSQSAdapter, AISQSAdapter)
       → apps.support.video.services.sqs_queue.VideoSQSQueue
       → Django ORM (Video.objects.filter().update())
```

**의존 방향**: Application → Port ← Infrastructure  
**문제**: ORM은 여전히 Adapter 체인 내부에 존재. Worker는 간접적으로 Django에 의존.

---

### 1단계: ORM 접근을 Infrastructure Repository로 격리 ✅ (Video 완료)

**목표**: Django ORM 호출을 `apps.support`가 아닌 `src.infrastructure.db`로 이동.

**구현 완료 (Video)**

```
src/infrastructure/db/
  video_repository.py   ✅ VideoRepository (mark_processing, complete_video, fail_video)
  ai_repository.py      ✅ AIJobRepository (mark_processing, complete_job, fail_job)

src/application/video/
  handler.py            ✅ ProcessVideoJobHandler
    → idempotency.acquire_lock → repo.mark_processing → process → repo.complete_video
```

**작업 항목**

- [x] `IVideoRepository` Port, `VideoRepository` 구현
- [x] `IAIJobRepository` Port, `AIJobRepository` 구현
- [x] `ProcessVideoJobHandler` 생성 → Video Worker는 handler.handle()만 호출
- [ ] AI Worker Handler 적용 (ProcessAIJobHandler)

---

### 2단계: Django 모델 상속 경로 정리 (TimestampModel)

**현재 문제**

```
Video → apps.api.common.models.TimestampModel
AIJobModel → apps.core.models (또는 api.common)
```

→ Worker가 간접적으로 `apps.api`에 붙는 원인.

**해결 전략 (택 1)**

| 옵션 | 설명 |
|------|------|
| A. core로 이동 | `TimestampModel`, `BaseModel`을 `apps.core.models.base`로 이전 (이미 부분 적용됨) |
| B. 완전 분리 | `src/shared/models/base_timestamp.py` 또는 `src/domain/entities/base.py`에 순수 Python 클래스 정의. ORM 모델은 Infrastructure에서 이 뼈대를 상속하도록 변경 |

**작업 항목**

- [ ] `Video`, `AIJobModel` 등 Worker 관련 모델이 `apps.api`를 통과하지 않도록 정리
- [ ] `INSTALLED_APPS` / import 경로로 `api.common` 의존성 제거
- [ ] Worker 경로에서 `apps.api` import 여부 재검증

**효과**

- Worker ↔ `apps.api` 완전 단절
- 모델 정의의 SSOT 확보

---

### 3단계: Queue Adapter 완전 교체 (Publisher / Consumer 분리)

**현재 구조**

```
API / Publisher  → apps.support.video.services.sqs_queue.VideoSQSQueue.enqueue()
Worker (Consumer) → VideoSQSAdapter → 같은 VideoSQSQueue.receive/delete/complete/fail
```

→ Publisher와 Consumer가 동일한 클래스에 섞여 있음.

**목표 구조**

```
API (Publisher)
  → apps.support.video.services.sqs_queue.VideoSQSQueue.enqueue()  # 유지 (또는 src.infrastructure.sqs.publisher)

Worker (Consumer)
  → src.infrastructure.sqs.video_consumer
      - boto3 직접 호출 (receive_message, delete_message)
      - 완료/실패는 Repository 호출 (mark_processing, complete_video, fail_video)
  → src.application.video.handler
  → src.infrastructure.db.video_repository
```

**작업 항목**

- [ ] `apps.support.video.services.sqs_queue`를 **enqueue 전용**으로 축소 (또는 Publisher 모듈로 분리)
- [ ] Worker 전용 `src/infrastructure/sqs/video_consumer.py` 생성
  - boto3 SQS receive / delete
  - DB 접근 없음 (Repository에 위임)
- [ ] `VideoSQSQueue`의 `complete_video`, `fail_video`, `mark_processing` 제거 → Repository로 이전
- [ ] AI 동일 적용

**효과**

- Publisher와 Consumer 분리
- Worker가 `apps.support` Queue 클래스에 의존하지 않음
- SQS 메시지 포맷은 그대로 유지

---

### 4단계: Redis 기반 상태 레이어 추가 ✅ (Video 완료)

**목표**: Worker 독립성 강화. 상태/진행률을 Redis로 먼저 기록, DB는 최종 결과용.

**구현 완료**

```
src/application/ports/
  idempotency.py        ✅ IIdempotency
  progress.py           ✅ IProgress (Write-Behind용)

src/infrastructure/cache/
  redis_idempotency_adapter.py   ✅ RedisIdempotencyAdapter (SETNX 락)
  redis_progress_adapter.py      ✅ RedisProgressAdapter (진행률 Redis만 기록)
```

**아키텍처 (Video Worker)**

```
Handler.handle()
  → idempotency.acquire_lock()   # Repository 호출 전 반드시
  → repo.mark_processing()
  → processor (progress.record_progress 각 단계)  # Write-Behind
  → repo.complete_video()        # 최종 DB 기록
  → idempotency.release_lock()
```

**작업 항목**

- [x] `IIdempotency`, `RedisIdempotencyAdapter`
- [x] `IProgress`, `RedisProgressAdapter`
- [x] Video Processor에서 progress.record_progress 호출 (downloading, transcoding, uploading 등)

---

### 5단계: Django-less Worker 이미지

**목표**: Worker 이미지에서 Django 제거.

**현재**

- Worker 진입 시 `DJANGO_SETTINGS_MODULE` 로딩
- Django ORM, `manage.py` 의존

**목표 이미지 구성**

```
Worker 이미지
  src/
    domain/
    application/
    infrastructure/   # DB Repository는 SQLAlchemy / raw SQL / 기타 ORM
    interfaces/workers/
  requirements-worker.txt   # Django 미포함
```

**전제 조건**

- 1~4단계 완료 (ORM → Repository, Queue 분리, Redis 상태 레이어)
- Repository 구현체가 **Django ORM 대신** SQLAlchemy, raw `psycopg2`, 또는 HTTP API 호출로 교체

**작업 항목**

- [ ] `VideoRepository` Django 의존 제거 → SQLAlchemy / raw SQL 구현
- [ ] `AIJobRepository` 동일
- [ ] `requirements-worker.txt`에서 Django, djangorestframework 제거
- [ ] Worker Dockerfile에서 `manage.py`, `apps/` 복사 제거
- [ ] 진입점: `python -m src.interfaces.workers.video.main` (Django 설정 없음)

**효과**

- Worker 이미지 경량화
- 배포/스케일링 독립
- Django 버전 업그레이드와 Worker 분리

---

## 📊 단계별 체크리스트 요약

| 단계 | 핵심 작업 | Worker 의존성 변화 |
|------|-----------|---------------------|
| 0 | (현재) Adapter → apps.support Queue | apps.support, Django ORM (간접) |
| 1 | ORM → Repository, Handler 도입 | apps.support (Queue만), Repository(ORM) |
| 2 | TimestampModel 경로 정리 | apps.api 제거 |
| 3 | Queue Consumer 분리, boto3 직접 | apps.support Queue 제거 |
| 4 | Redis Port/Adapter | libs.redis 직접 의존 제거 |
| 5 | Django-less 이미지 | Django, ORM 완전 제거 |

---

## 🔗 관련 문서

- [HEXAGONAL_ARCHITECTURE.md](./HEXAGONAL_ARCHITECTURE.md) - 현재 구조
- [WORKER_ARCHITECTURE_FACT_REPORT.md](./WORKER_ARCHITECTURE_FACT_REPORT.md) - 워커 현황
