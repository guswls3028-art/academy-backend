# Hexagonal (Clean) 아키텍처 리팩토링

## 폴더 구조

```
academy/
├── src/                          # Hexagonal 레이어 (신규)
│   ├── domain/                   # 순수 비즈니스 로직, 엔티티 (Django 의존성 없음)
│   │   ├── video/
│   │   ├── ai/
│   │   └── messaging/
│   │
│   ├── application/              # 유스케이스 서비스 (Service 레이어)
│   │   └── ports/                # Port (인터페이스) - Infrastructure가 구현
│   │       ├── video_queue.py    # IVideoQueue
│   │       ├── ai_queue.py       # IAIQueue
│   │       ├── video_repository.py  # IVideoRepository
│   │       ├── ai_repository.py     # IAIJobRepository
│   │       ├── idempotency.py       # IIdempotency
│   │       └── progress.py          # IProgress (Write-Behind)
│   │
│   ├── application/video/
│   │   └── handler.py               # ProcessVideoJobHandler
│   │
│   ├── infrastructure/
│   │   ├── video/
│   │   │   ├── sqs_adapter.py       # IVideoQueue
│   │   │   └── processor.py         # process_video
│   │   ├── ai/
│   │   │   └── sqs_adapter.py       # AISQSAdapter
│   │   ├── db/
│   │   │   ├── video_repository.py  # VideoRepository
│   │   │   └── ai_repository.py     # AIJobRepository
│   │   └── cache/
│   │       ├── redis_idempotency_adapter.py
│   │       └── redis_progress_adapter.py
│   │
│   └── interfaces/               # API(Views, Serializers) 및 Workers(SQS Consumer)
│       # 현재: apps/worker가 Worker 역할, apps/api가 API 역할
│       # 향후: src/interfaces/workers, src/interfaces/api로 이전 가능
│
├── apps/                         # Django 앱 (기존 유지)
│   ├── api/                      # API 설정, WSGI
│   ├── core/                     # 공통 모델
│   ├── domains/                  # 도메인별 Django 앱
│   ├── support/                  # Video, AI, Messaging 지원
│   └── worker/                   # Worker 진입점 (src.infrastructure 사용)
│
└── libs/                         # 공통 라이브러리 (Queue, Redis, S3)
```

## 의존성 역전 (DIP)

- **Application (ports)**: `IVideoQueue`, `IAIQueue` 등 추상 인터페이스 정의
- **Infrastructure**: 위 포트를 구현하는 `VideoSQSAdapter`, `AISQSAdapter`
- **Worker**: `src.infrastructure`에서 어댑터를 주입받아 사용
- Application은 Infrastructure 구체 클래스가 아니라 **인터페이스**에만 의존

## Worker 격리

각 워커(Video, AI)는 다음만 참조:

- `src.infrastructure.*` - 어댑터 (Repository, SQS, Redis Idempotency/Progress)
- `src.application.video.handler` - ProcessVideoJobHandler (Video Worker)
- `apps.worker.*.config` - 설정
- `apps.worker.*.ai` - AI 파이프라인 (AI Worker 전용)

**금지**: `apps.api`, `rest_framework`, `.views`, `.serializers`, `django.urls`

## 변경 사항 요약

| 항목 | 변경 전 | 변경 후 |
|------|---------|---------|
| Video Worker Queue | `apps.support.video.services.sqs_queue.VideoSQSQueue` | `src.infrastructure.video.VideoSQSAdapter` |
| AI Worker Queue | `apps.support.ai.services.sqs_queue.AISQSQueue` | `src.infrastructure.ai.AISQSAdapter` |
| Docker base | `COPY apps libs manage.py` | `COPY src apps libs manage.py` |

## SQS 메시지 포맷 / DB 스키마

**변경 없음**. 기존 `VideoSQSQueue`, `AISQSQueue`를 어댑터가 래핑하여 동일한 동작 유지.

## 검증

```powershell
python scripts/check_worker_forbidden_imports.py   # OK
python scripts/check_workers.py                    # OK
.\docker\build.ps1                                 # 빌드 성공
```
