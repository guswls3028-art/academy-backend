# AWS 500 가이드 + Docker·requirements 기계 정렬 (실제 코드 기준)

**역할**: `AWS_500_START_DEPLOY_GUIDE.md` §6~§10과 실제 Dockerfile·requirements·설정 경로를 **기계적으로 일치**시킨 참조. 배포·이미지 최적화 진행 시 이 문서만 따라도 코드와 100% 일치.

**원칙**: 코드·파일 경로·COPY/CMD는 grep·파일 내용 기준. 가이드 문구는 수정하지 않고, 여기서 "실제 값"만 나열.

---

## 1. Dockerfile ↔ 이미지·빌드 순서 (실제 코드)

| 서비스 | Dockerfile 경로 | FROM | ECR 이미지 이름(가이드) | 빌드 순서 |
|--------|-----------------|------|-------------------------|-----------|
| 베이스 | `docker/Dockerfile.base` | python:3.11-slim (builder + runtime) | — | 1 (최초 1회) |
| API | `docker/api/Dockerfile` | academy-base:latest | academy-api:latest | 2 (베이스 이후) |
| Messaging Worker | `docker/messaging-worker/Dockerfile` | academy-base:latest | academy-messaging-worker:latest | 2 (베이스 이후) |
| Video Worker | `docker/video-worker/Dockerfile` | academy-base:latest | academy-video-worker:latest | 2 (베이스 이후) |
| AI Worker CPU | `docker/ai-worker-cpu/Dockerfile` | academy-base:latest | academy-ai-worker-cpu:latest (또는 academy-ai-worker:latest) | 2 (베이스 이후) |

- **ARM64 빌드**(EC2 t4g): `docker buildx build --platform linux/arm64 -f <Dockerfile> -t <이미지>:latest --load .` (컨텍스트: 프로젝트 루트)
- **베이스 먼저**: `docker/Dockerfile.base` → `academy-base:latest` 생성 후, API/Messaging/Video/AI Worker CPU Dockerfile 빌드.

---

## 2. Dockerfile별 requirements (실제 COPY·파일 존재)

| Dockerfile | COPY하는 requirements | 실제 파일 존재 |
|------------|------------------------|----------------|
| `docker/Dockerfile.base` | `requirements/common.txt` | ✅ `requirements/common.txt` |
| `docker/api/Dockerfile` | `requirements/common.txt`, `requirements/api.txt` | ✅ 둘 다 존재 |
| `docker/messaging-worker/Dockerfile` | `requirements/common.txt`, `requirements/worker-messaging.txt` | ✅ 둘 다 존재 |
| `docker/video-worker/Dockerfile` | `requirements/common.txt`, `requirements/worker-video.txt` | ✅ 둘 다 존재 |
| `docker/ai-worker-cpu/Dockerfile` | `common.txt`, `worker-ai-common.txt`, `worker-ai-cpu.txt`, `worker-ai-excel.txt` | ✅ `requirements/` 하위 전부 존재 |

---

## 3. CMD·엔트리포인트 (실제 Dockerfile)

| 서비스 | CMD (실제) |
|--------|------------|
| API | `gunicorn ... apps.api.config.wsgi:application` |
| Messaging Worker | `python -m apps.worker.messaging_worker.sqs_main` |
| Video Worker | `python -m apps.worker.video_worker.sqs_main` |
| AI Worker CPU | `python -m apps.worker.ai_worker.sqs_main_cpu` |

---

## 4. DJANGO_SETTINGS_MODULE (가이드·코드 일치)

| 대상 | 값 (실제 코드·가이드 §7 §8 §9 §10) |
|------|-------------------------------------|
| API 서버 | `apps.api.config.settings.prod` (또는 .env 기본값) |
| Worker 전용 (Messaging·Video·AI) | `apps.api.config.settings.worker` |

- 설정 파일 실제 경로: `apps/api/config/settings/base.py`, `worker.py`, `prod.py`, `dev.py`.

---

## 5. §10 환경 변수 ↔ .env.example (기계 대조)

가이드 §10에 나열된 항목과 `.env.example` 키 일치 여부.

| §10 항목 | .env.example 키 | 비고 |
|----------|------------------|------|
| SECRET_KEY, DEBUG | ✅ | |
| DB_HOST, DB_NAME, DB_USER, DB_PASSWORD, DB_PORT, DB_CONN_MAX_AGE | ✅ | |
| R2_ACCESS_KEY, R2_SECRET_KEY, R2_ENDPOINT, R2_PUBLIC_BASE_URL, R2_AI_BUCKET, R2_VIDEO_BUCKET, R2_EXCEL_BUCKET | ✅ | |
| INTERNAL_WORKER_TOKEN, API_BASE_URL | ✅ | |
| AWS_REGION, VIDEO_SQS_QUEUE_NAME, MESSAGING_SQS_QUEUE_NAME, AI 큐 이름들 | ✅ AI_SQS_QUEUE_NAME_LITE/BASIC/PREMIUM, MESSAGING_SQS_QUEUE_NAME | |
| EC2_IDLE_STOP_THRESHOLD | ✅ (Video, AI 워커만) | |
| Worker 전용: DJANGO_SETTINGS_MODULE, VIDEO_WORKER_ID, AI_WORKER_ID_CPU, MESSAGING_WORKER_ID | ✅ | .env에는 선택; 런타임에 -e 로 덮어써도 됨 |

---

## 6. 가이드 §6.2 빌드 명령 ↔ 실제 경로

- **베이스**: `docker buildx build --platform linux/arm64 -f docker/Dockerfile.base -t academy-base:latest --load .`
- **API**: `docker buildx build --platform linux/arm64 -f docker/api/Dockerfile -t academy-api:latest --load .`
- **Messaging Worker**: `docker buildx build --platform linux/arm64 -f docker/messaging-worker/Dockerfile -t academy-messaging-worker:latest --load .`
- **Video Worker**: `docker buildx build --platform linux/arm64 -f docker/video-worker/Dockerfile -t academy-video-worker:latest --load .`
- **AI Worker CPU**: `docker buildx build --platform linux/arm64 -f docker/ai-worker-cpu/Dockerfile -t academy-ai-worker-cpu:latest --load .` (또는 academy-ai-worker:latest)

---

## 7. SQS 큐 이름 (스크립트·설정과 일치)

| 용도 | 큐 이름 (실제) | 스크립트 |
|------|----------------|----------|
| Video | academy-video-jobs | create_sqs_resources.py |
| Messaging | academy-messaging-jobs | create_sqs_resources.py |
| AI Lite/Basic/Premium | academy-ai-jobs-lite, academy-ai-jobs-basic, academy-ai-jobs-premium | create_ai_sqs_resources.py |

---

## 8. 진행 가능 여부·체크

- **AWS_500_START_DEPLOY_GUIDE.md** (1~498): §6~§10은 위 1~7과 **기계적으로 일치**하면 "바로 진행 가능".
- **도커 이미지 최적화**: Dockerfile·requirements 실제 코드 기준 위 표 준수 시, 최적화(레이어 캐시·.dockerignore·multi-stage)는 **이 구조 유지**하면서 적용.
- **상수 변경 금지**: LEASE 3540, visibility 3600, inference_max 3600 (CODE_ALIGNED_SSOT §2.5).

**결론**: 현재 상태에서 위 표대로만 사용하면 **진행 바로 가능**. 신규 문서는 `docs/cursor_docs/`에만 추가(README 규칙).
