# Worker 이상향 리팩토링 설계

## 1. 레이어 구조

```
apps/
├── api/                    # Presentation (API 전용)
│   ├── config/             # settings, wsgi
│   ├── common/             # → base models를 core로 이동 후 최소화
│   ├── v1/                 # urls, routers
│   └── ...
├── core/                   # Domain 공통 + Infrastructure
│   ├── models/
│   │   ├── base.py         # TimestampModel, BaseModel (api.common에서 이동)
│   │   ├── tenant.py
│   │   └── ...
│   └── ...
├── domains/                # Domain (ai, lectures, ...)
├── support/                # Domain Support (video, messaging, ai)
│   ├── video/services/     # VideoSQSQueue 등
│   ├── ai/services/        # AISQSQueue
│   └── messaging/          # services, credit_services, models
├── shared/                 # contracts, DTO
│   └── contracts/
└── worker/                 # Worker Runtime
    ├── video_worker/sqs_main.py
    ├── ai_worker/sqs_main_cpu.py, sqs_main_gpu.py
    └── messaging_worker/sqs_main.py
```

**경계**:
- Worker는 `apps.api`, `rest_framework`, `*views`, `*serializers`, `*urls` import 금지
- Worker는 `apps.support`, `apps.domains`, `apps.core`, `apps.shared`만 사용
- `TimestampModel`/`BaseModel`을 `apps.core.models.base`로 이동 → `apps.api` 제거

## 2. Requirements 분리

| 파일 | 내용 |
|------|------|
| common.txt | requests, boto3, pydantic, redis, Django, psycopg2-binary, sqlparse, asgiref (API/Worker 공통) |
| api.txt | -r ./common.txt + djangorestframework, gunicorn, drf-yasg, solapi 등 API 전용 |
| worker-video.txt | -r ./common.txt + ffmpeg-python, pillow, opencv, ... |
| worker-ai.txt | -r ./common.txt + numpy, torch, pytesseract, ... |
| worker-messaging.txt | -r ./common.txt + solapi |

**핵심**: Worker 이미지에 `-r api.txt` 사용 금지 → djangorestframework, gunicorn 등 미포함

## 3. Dockerfile 전략

1. `COPY requirements/ ./requirements/` 먼저
2. `pip install -r requirements/<해당>.txt` (api/worker-video/worker-ai/worker-messaging)
3. `COPY apps libs manage.py` 후반
4. 캐시 효율: 코드 변경 시에만 재빌드

## 4. Forbidden Import 테스트

`scripts/check_worker_forbidden_imports.py`:
- `apps/worker/**`에서 `apps.api`, `rest_framework`, `*views`, `*serializers` 등 검사
- 있으면 exit 1

## 5. 런타임 의존성 점검

`scripts/check_worker_deps.sh` 또는 `check_worker_deps.ps1`:
- worker 이미지에서 `pip freeze | grep -E "djangorestframework|gunicorn|drf-yasg"` → 없어야 함
