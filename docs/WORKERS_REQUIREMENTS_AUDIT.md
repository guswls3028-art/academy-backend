# 3개 워커 Requirements 점검 리포트

**대상**: Messaging Worker, Video Worker, AI Worker  
**목적**: requirements 및 Docker 구성 정합성 점검

---

## 1. 현황 요약

| 워커 | requirements | Dockerfile | docker-compose |
|------|-------------|------------|----------------|
| **Video** | worker-video.txt | ✅ video-worker | ✅ video-worker |
| **AI** | worker-ai.txt | ✅ ai-worker | ✅ ai-worker-cpu, ai-worker-gpu |
| **Messaging** | worker-messaging.txt | ❌ 없음 | ❌ 없음 |

---

## 2. Requirements 의존성 체인

### common.txt (공통)
- requests, boto3, pydantic, python-dotenv 등
- 모든 워커가 필요

### api.txt
- `-r common.txt`
- Django, DRF, psycopg2, gunicorn, solapi 등
- **Video Worker**: worker-video가 `-r api.txt` 포함 → ✅
- **AI Worker**: worker-ai에 api.txt 없음 → ❌ **Django 누락**
- **Messaging Worker**: worker-messaging에 api.txt 없음 → Django 필요 여부 확인

### worker-video.txt
```
-r common.txt
-r api.txt
Django, psycopg2, ffmpeg-python, pillow, opencv, requests, djangorestframework...
```
- VideoSQSQueue가 django.conf, django.db, apps.support.video.models.Video 사용
- **결론**: ✅ 적절함

### worker-ai.txt
```
-r common.txt
numpy, scipy, pillow, pytesseract, google-cloud-vision, torch, sentence-transformers...
```
- AISQSQueue가 django.conf, django.db, apps.domains.ai.models.AIJobModel 사용
- **결론**: ❌ **Django 누락** — `-r api.txt` 또는 최소 Django+psycopg2 추가 필요

### worker-messaging.txt
```
-r common.txt
redis, solapi
```
- Messaging worker가 Django ORM 사용 (is_reservation_cancelled, get_tenant_messaging_info, NotificationLog)
- **결론**: ❌ **Django 누락** — `-r api.txt` 추가 필요

---

## 3. 코드 의존성 정리

### Video Worker
| 모듈 | 필요 패키지 |
|------|------------|
| apps.support.video.services.sqs_queue | Django, libs.queue (boto3) |
| apps.support.video.models | Django, psycopg2 |
| libs.redis.idempotency | redis |
| libs.s3_client.presign | boto3 |

### AI Worker
| 모듈 | 필요 패키지 |
|------|------------|
| apps.support.ai.services.sqs_queue | Django, libs.queue |
| apps.domains.ai.models | Django, psycopg2 |
| ai.ocr (tesseract, google) | pytesseract, google-cloud-vision |
| ai.embedding, ai.problem | torch, sentence-transformers, openai |
| ai.detection | onnxruntime, opencv |
| libs.redis.idempotency | redis |

### Messaging Worker
| 모듈 | 필요 패키지 |
|------|------------|
| libs.queue | boto3 |
| libs.redis.idempotency | redis |
| apps.support.messaging.* | Django, solapi |
| apps.core.models.Tenant | Django |

---

## 4. 발견 이슈

### 4.1 worker-ai.txt — Django 누락
- **문제**: AISQSQueue가 `django.conf.settings`, `django.db.transaction`, `AIJobModel` 사용
- **조치**: `-r api.txt` 추가 또는 최소 `Django`, `psycopg2-binary`, `djangorestframework` 등 추가

### 4.2 worker-messaging.txt — Django 누락
- **문제**: NotificationLog, Tenant, credit_services 등 Django ORM 사용
- **조치**: `-r api.txt` 추가

### 4.3 Messaging Worker — Docker 부재
- **문제**: docker-compose에 messaging-worker 없음, Dockerfile 없음
- **조치**: docker/messaging-worker/Dockerfile 추가, docker-compose에 messaging-worker 서비스 추가

### 4.4 ai_worker storage 패키지
- `apps.worker.ai_worker.storage.downloader` 사용 중
- `storage/__init__.py` 없을 수 있음 → Python 3.3+ namespace 패키지로 동작 가능하나, `__init__.py` 두는 편이 안전

### 4.5 Video Worker import 시 Django 초기화
- Docker 검사 시 `python -c "import apps.worker.video_worker.sqs_main"`만 실행
- VideoSQSQueue 로딩 시 Django settings/model 로드 필요
- **조치**: `django.setup()` 선행 호출 후 import

---

## 5. 적용된 수정 사항 (완료)

- [x] **worker-ai.txt**: `-r api.txt` 추가
- [x] **worker-messaging.txt**: `-r api.txt` 추가
- [x] **ai-worker Dockerfile**: api.txt COPY 추가
- [x] **Messaging Worker Dockerfile** 생성 (`docker/messaging-worker/`)
- [x] **docker-compose**에 messaging-worker 서비스 추가
- [x] **build.ps1**에 messaging-worker 빌드 단계 추가
- [x] **check_workers.py**: `django.setup()` 선행, Messaging Worker 검증 추가
- [x] **apps.worker.ai_worker.storage/__init__.py** 추가
