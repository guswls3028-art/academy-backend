# 아키텍처 및 인프라 (코드 기반)

**기준**: 실제 코드·설정·스크립트 경로. 배포 따라하기는 [500명 스타트 가이드](cursor_docs/AWS_500_START_DEPLOY_GUIDE.md) 참고.

---

## 1. 구성도

```
Internet → Cloudflare CDN
              ├── Frontend (Static)
              └── API (Django/Gunicorn)
                    ├── RDS PostgreSQL
                    ├── R2 (academy-ai, academy-video, academy-excel, academy-storage)
                    └── SQS
                          ├── academy-video-jobs      → Video Worker
                          ├── academy-ai-jobs-lite    ┐
                          ├── academy-ai-jobs-basic  ├→ AI Worker CPU
                          ├── academy-ai-jobs-premium┘→ AI Worker GPU (향후)
                          └── academy-messaging-jobs → Messaging Worker
```

---

## 2. 설정 진입점 (코드)

| 용도 | 경로 |
|------|------|
| API 설정 | `apps/api/config/settings/base.py`, `prod.py` |
| Worker 설정 | `apps/api/config/settings/worker.py` |
| 큐 이름 기본값 | `apps/api/config/settings/worker.py` (105–109행) |
| R2 버킷 | `apps/api/config/settings/base.py` (299–303행) |

---

## 3. SQS 큐 (코드와 일치)

| 큐 | 기본 이름 | DLQ | 정의 위치 |
|----|-----------|-----|-----------|
| Video | `academy-video-jobs` | `academy-video-jobs-dlq` | `apps/support/video/services/sqs_queue.py` |
| AI Lite | `academy-ai-jobs-lite` | `academy-ai-jobs-lite-dlq` | `apps/support/ai/services/sqs_queue.py` |
| AI Basic | `academy-ai-jobs-basic` | `academy-ai-jobs-basic-dlq` | 위 동일 |
| AI Premium | `academy-ai-jobs-premium` | `academy-ai-jobs-premium-dlq` | 위 동일 |
| Messaging | `academy-messaging-jobs` | `academy-messaging-jobs-dlq` | `apps/support/messaging/sqs_queue.py` |

**생성 스크립트**
```bash
python scripts/create_sqs_resources.py ap-northeast-2   # video + messaging
python scripts/create_ai_sqs_resources.py ap-northeast-2 # ai 3-tier
```

---

## 4. R2 버킷 (코드와 일치)

| 용도 | 환경 변수 | 기본값 |
|------|-----------|--------|
| AI 결과 | `R2_AI_BUCKET` | `academy-ai` |
| 비디오/HLS | `R2_VIDEO_BUCKET` | `academy-video` |
| 엑셀 업로드/내보내기 | `R2_EXCEL_BUCKET` | `academy-excel` |
| 기타 스토리지 | `R2_STORAGE_BUCKET` | `academy-storage` |

`.env.example`, `apps/api/config/settings/base.py` 참고.

---

## 5. 워커 진입점

| 워커 | 진입점 |
|------|--------|
| Video | `apps/worker/video_worker/sqs_main.py` |
| Messaging | `apps/worker/messaging_worker/sqs_main.py` |
| AI CPU | `apps/worker/ai_worker/sqs_main_cpu.py` |
| AI GPU | `apps/worker/ai_worker/sqs_main_gpu.py` |

Worker는 `DJANGO_SETTINGS_MODULE=apps.api.config.settings.worker` 사용. `apps.api`는 import하지 않음.

---

## 6. Docker 빌드 (실제 스크립트 기준)

**순서**: base → api → video-worker → ai-worker → messaging-worker. (build.ps1: base → api → ai-worker → video-worker → messaging-worker)

```powershell
.\docker\build.ps1
```

수동:
```bash
docker build -f docker/Dockerfile.base -t academy-base:latest .
docker build -f docker/api/Dockerfile -t academy-api:latest .
docker build -f docker/video-worker/Dockerfile -t academy-video-worker:latest .
docker build -f docker/ai-worker/Dockerfile -t academy-ai-worker:latest .
docker build -f docker/messaging-worker/Dockerfile -t academy-messaging-worker:latest .
```

AI 전용 EC2용: `docker/ai-worker-cpu/Dockerfile`, `docker/ai-worker-gpu/Dockerfile` → `-AiWorkerCpu`, `-AiWorkerGpu` 옵션.

---

## 7. 인프라 요약

- **API**: Gunicorn + Gevent, stateless. RDS·R2·SQS 사용.
- **DB**: PostgreSQL (RDS). Redis는 선택(미설정 시 DB fallback).
- **리전**: ap-northeast-2 (서울). SQS·RDS·EC2 동일 리전 권장.
- **비용 절감**: SQS Long Polling 20초, EC2 Self-Stop (Video/AI Worker), R2 사용(S3 대비).

상세 배포 단계·보안 그룹·EC2 스펙은 [cursor_docs/AWS_500_START_DEPLOY_GUIDE.md](cursor_docs/AWS_500_START_DEPLOY_GUIDE.md) 참고.
