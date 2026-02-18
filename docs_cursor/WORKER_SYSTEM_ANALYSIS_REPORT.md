# Academy 워커 시스템 통합 분석 보고서

> **목적**: 실제 코드 기반으로 API·워커·배포환경·연결 관계를 통합 분석. GPT 검사용.
> **작성일**: 2026-02-18

---

## 1. 시스템 개요

| 구성요소 | 역할 | 엔트리포인트 (실제 코드) |
|----------|------|---------------------------|
| **API** | Django REST API, SQS enqueue, Redis 진행률 조회 | gunicorn → `apps.api` |
| **Video Worker** | 영상 인코딩 (HLS), SQS Long Polling | `apps.worker.video_worker.sqs_main` |
| **AI Worker** | OCR/엑셀 등 AI 작업, SQS Long Polling | `apps.worker.ai_worker.sqs_main_cpu` → `academy.framework.workers.ai_sqs_worker.run_ai_sqs_worker` |
| **Messaging Worker** | Solapi SMS/LMS/Alimtalk 발송 | `apps.worker.messaging_worker.sqs_main` |

---

## 2. 연결 아키텍처 (실제 코드 기반)

```
                    ┌─────────────────────────────────────────────────────────────┐
                    │                     RDS PostgreSQL (DB)                       │
                    │  - Video, AIJob, Messaging, core 등 모든 ORM                  │
                    └───────────────────────┬─────────────────────────────────────┘
                                            │
  ┌─────────────────────────────────────────┼─────────────────────────────────────┐
  │                                         │                                       │
  │  API (Django)                           │                                       │
  │  - libs.queue.SQSQueueClient            │                                       │
  │  - libs.redis.client.get_redis_client   │                                       │
  │  - VideoSQSQueue().enqueue              │                                       │
  │  - AISQSQueue().enqueue                 │                                       │
  │  - MessagingSQSQueue().enqueue          │                                       │
  └───┬─────────────────┬───────────────────┼──────────────────┬───────────────────┘
      │                 │                   │                  │
      │ SQS send        │ SQS send          │ DB read/write    │ Redis read (진행률)
      ▼                 ▼                   │                   ▼
┌──────────────┐ ┌──────────────┐          │            ┌──────────────────────────┐
│ academy-     │ │ academy-ai-  │          │            │ ElastiCache Redis        │
│ video-jobs   │ │ jobs-lite    │          │            │ - job:{id}:lock (멱등)   │
│              │ │ jobs-basic   │          │            │ - job:{id}:progress      │
└──────┬───────┘ └──────┬───────┘          │            └────────────┬─────────────┘
       │                │                  │                         │
       │                │                  │                         │
       ▼                ▼                  │                         │
┌──────────────┐ ┌──────────────┐ ┌────────┴────────┐ ┌──────────────┴──────────────┐
│ academy-     │ │              │ │                 │ │                              │
│ messaging-   │ │              │ │                 │ │                              │
│ jobs         │ │              │ │                 │ │                              │
└──────┬───────┘ │              │ │                 │ │                              │
       │         │              │ │                 │ │                              │
       ▼         ▼              ▼ ▼                 ▼ ▼                              ▼
┌─────────────┐ ┌─────────────┐ ┌─────────────┐ ┌────────────────────────────────────┐
│ Video       │ │ AI          │ │ Messaging   │ │ 공통: libs.redis.client             │
│ Worker      │ │ Worker      │ │ Worker      │ │ - RedisIdempotencyAdapter (Video)  │
│             │ │             │ │             │ │ - RedisProgressAdapter (Video, AI) │
│ - SQS poll  │ │ - SQS poll  │ │ - SQS poll  │ │ - acquire_job_lock (Messaging)     │
│ - Redis     │ │ - Redis     │ │ - Redis     │ │ - REDIS_HOST env (ElastiCache)     │
│   lock/prog │ │   progress  │ │   lock only │ └────────────────────────────────────┘
│ - DB repo   │ │ - DB UoW    │ │ - Solapi    │
└─────────────┘ └─────────────┘ └─────────────┘
```

---

## 3. SQS 큐 (실제 코드 참조)

| 큐 이름 | 코드 위치 | Producer (enqueue) | Consumer |
|---------|-----------|--------------------|----------|
| `academy-video-jobs` | `apps.support.video.services.sqs_queue.VideoSQSQueue` | `video_views.py` (encode, delete_r2) | Video Worker |
| `academy-ai-jobs-lite` | `apps.support.ai.services.sqs_queue.AISQSQueue` | `domains.ai.queueing.publisher` | AI Worker (weighted poll) |
| `academy-ai-jobs-basic` | 위와 동일 | 위와 동일 | AI Worker |
| `academy-ai-jobs-premium` | 위와 동일 | 위와 동일 | AI Worker GPU (미사용 가능) |
| `academy-messaging-jobs` | `apps.support.messaging.sqs_queue.MessagingSQSQueue` | `messaging.services.enqueue_sms` | Messaging Worker |

**설정**: `apps.api.config.settings.base.py` 340~345행  
**환경변수**: `VIDEO_SQS_QUEUE_NAME`, `AI_SQS_QUEUE_NAME_LITE/BASIC/PREMIUM`, `MESSAGING_SQS_QUEUE_NAME`

---

## 4. Redis 사용 (실제 코드 참조)

| 용도 | 코드 위치 | 키 패턴 | TTL |
|------|-----------|---------|-----|
| 멱등 락 (Video) | `src.infrastructure.cache.redis_idempotency_adapter.RedisIdempotencyAdapter` | `job:encode:{video_id}:lock` | 4h (VIDEO_LOCK_TTL) |
| 멱등 락 (Messaging) | `libs.redis.idempotency.acquire_job_lock` | `job:{job_id}:lock` | 30분 |
| 진행률 (Video) | `src.infrastructure.cache.redis_progress_adapter.RedisProgressAdapter` | `job:video:{video_id}:progress` | 4h |
| 진행률 (AI) | 위와 동일 | `job:{job_id}:progress` | 1h |
| Job 상태 (libs) | `libs.redis.job_status` | `job:{job_id}:status` | 1h |

**연결**: `libs.redis.client.get_redis_client()`  
**환경변수**: `REDIS_HOST`, `REDIS_PORT`, `REDIS_PASSWORD`, `REDIS_DB`  
**인프라**: ElastiCache Redis (Single-AZ, cache.t4g.micro), `scripts/setup_elasticache_redis.ps1`

---

## 5. DB (PostgreSQL) 연결

| 역할 | 설정 |
|------|------|
| 엔진 | `django.db.backends.postgresql` |
| 환경변수 | `DB_HOST`, `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_PORT` |
| 연결 풀 | `CONN_MAX_AGE=60` (1분) |
| 사용처 | API, Video Worker (DjangoVideoRepository), AI Worker (DjangoUnitOfWork), Messaging Worker (Django ORM) |

---

## 6. 워커별 상세 (실제 코드)

### 6.1 Video Worker

- **엔트리**: `apps.worker.video_worker.sqs_main.main()`
- **큐**: `academy-video-jobs` (VideoSQSAdapter → VideoSQSQueue)
- **처리 흐름**: SQS receive → ProcessVideoJobHandler.handle → Idempotency(Redis) → mark_processing(DB) → process_video(Redis progress) → complete_video(DB) → release_lock(Redis)
- **Visibility**: 3h (`VIDEO_SQS_VISIBILITY_EXTEND=10800`)
- **셀프스탑**: 없음 (ASG 대응)
- **의존성**: RDS, Redis, SQS, R2(S3 호환), FFmpeg

### 6.2 AI Worker

- **엔트리**: `apps.worker.ai_worker.sqs_main_cpu` → `run_ai_sqs_worker()`
- **큐**: `academy-ai-jobs-lite`, `academy-ai-jobs-basic` (weighted poll: basic 3 / lite 1)
- **처리 흐름**: SQS receive → prepare_ai_job → SQSVisibilityExtender (60초마다 연장) → handle_ai_job (dispatcher) → complete/fail (DB) → delete SQS
- **Visibility**: 1h, lease 3540s, inference max 3600s
- **셀프스탑**: 없음 (ASG 대응)
- **의존성**: RDS, Redis(진행률), SQS, R2, AI 모델

### 6.3 Messaging Worker

- **엔트리**: `apps.worker.messaging_worker.sqs_main.main()`
- **큐**: `academy-messaging-jobs`
- **처리 흐름**: SQS receive → acquire_job_lock(Redis) → Solapi 발송 → release_lock
- **셀프스탑**: 없음 (ASG 대응)
- **의존성**: RDS, Redis(멱등), SQS, Solapi API

---

## 7. 배포 환경 (실제 스크립트)

### 7.1 모드

| 모드 | 스크립트 | 설명 |
|------|----------|------|
| 고정 EC2 + SSH | `scripts/full_redeploy.ps1` (WorkersViaASG=false) | academy-api, academy-video-worker, academy-ai-worker-cpu, academy-messaging-worker 각 EC2에 SSH → docker run |
| ASG Worker | `scripts/full_redeploy.ps1 -WorkersViaASG` | API만 SSH, 워커는 ASG instance refresh |
| Worker ASG 인프라 | `scripts/redeploy_worker_asg.ps1` | Lambda, Launch Template, ASG 배포 |
| SSM/IAM | `scripts/setup_worker_iam_and_ssm.ps1` | .env → SSM upload, EC2 role 정책 부여 |

### 7.2 인스턴스/컨테이너

| 이름 | 이미지 | 실행 명령 |
|------|--------|-----------|
| academy-api | academy-api:latest | gunicorn, 포트 8000 |
| academy-video-worker | academy-video-worker:latest | `python -m apps.worker.video_worker.sqs_main` |
| academy-ai-worker-cpu | academy-ai-worker-cpu:latest | `python -m apps.worker.ai_worker.sqs_main_cpu` |
| academy-messaging-worker | academy-messaging-worker:latest | `python -m apps.worker.messaging_worker.sqs_main` |

**공통 env**: `DJANGO_SETTINGS_MODULE=apps.api.config.settings.worker`, `--env-file .env`  
**Video 전용**: `-v /mnt/transcode:/tmp`, `--memory 4g`

### 7.3 네트워크 (실제 값)

- **Region**: ap-northeast-2
- **Subnets**: subnet-07a8427d3306ce910, subnet-09231ed7ecf59cfa4
- **Security Group (API/Worker)**: sg-02692600fbf8e26f7
- **Redis SG**: academy-redis-sg, 6379 inbound from sg-02692600fbf8e26f7
- **VPC**: vpc-0831a2484f9b114c2

---

## 8. ASG + Lambda (실제 코드)

### 8.1 queue_depth_lambda

- **위치**: `infra/worker_asg/queue_depth_lambda/lambda_function.py`
- **트리거**: EventBridge rate(1분)
- **역할**: SQS 큐 깊이 → CloudWatch 메트릭 + AI 워커 ASG desired capacity 조정
- **큐**: academy-ai-jobs-lite, academy-ai-jobs-basic, academy-video-jobs, academy-messaging-jobs
- **ASG**: academy-ai-worker-asg (visible+in_flight > 0 → desired 1~MAX, else desired 1 상시)

### 8.2 Worker EC2 IAM 정책

- **위치**: `infra/worker_asg/iam_policy_ec2_worker.json`
- **권한**: SSM GetParameter, ECR pull, CloudWatch Logs

---

## 9. API → Worker 연결 요약

| API 액션 | 큐 | 워커 | 코드 경로 |
|----------|-----|------|-----------|
| 비디오 인코딩 요청 | academy-video-jobs | Video | `video_views.py` → `VideoSQSQueue().enqueue` |
| R2 삭제 | academy-video-jobs | Video | `video_views.py` → `VideoSQSQueue().enqueue_delete_r2` |
| AI job 생성 | academy-ai-jobs-* | AI | `domains.ai.queueing.publisher` → `AISQSQueue().enqueue` |
| 메시지 발송 | academy-messaging-jobs | Messaging | `messaging.services.enqueue_sms` → `MessagingSQSQueue().enqueue` |

---

## 10. 진행률/상태 조회 (Redis vs DB)

| 조회 대상 | 저장소 | API | 코드 |
|-----------|--------|-----|------|
| 비디오 인코딩 % | Redis | serializer `encoding_progress` | `encoding_progress.get_video_encoding_progress` |
| AI job 진행률 | Redis | `GET /api/v1/core/job_progress/<id>/` | `RedisProgressAdapter.get_progress` |
| AI job 최종 상태 | DB | `GET /api/v1/jobs/<id>/` | `build_job_status_response` (DB + Redis progress 병합) |
| 비디오 시청 정책 | DB | access_resolver | `VideoAccess`, `VideoProgress`, `Attendance` |

---

## 11. 검증 포인트 (GPT 검사용)

1. **역할 분리**: 권한/정책 → DB, 진행률/락 → Redis, 최종 상태 → DB (CQRS-lite 일치 여부)
2. **연결 일관성**: API·워커가 동일 큐 이름·동일 Redis·동일 DB 사용하는지
3. **ASG 연동**: queue_depth_lambda → ASG desired, 워커 셀프스탑 제거 여부
4. **Redis 장애 전략**: `get_redis_client()` None 시 fallback(락 허용) 설계 의도 확인
5. **배포 경로**: full_redeploy vs redeploy_worker_asg vs setup_worker_iam_and_ssm 역할 구분

---

## 12. 관련 파일 인덱스

| 영역 | 경로 |
|------|------|
| Video Worker | `apps/worker/video_worker/sqs_main.py` |
| AI Worker | `apps/worker/ai_worker/sqs_main_cpu.py`, `academy/framework/workers/ai_sqs_worker.py` |
| Messaging Worker | `apps/worker/messaging_worker/sqs_main.py` |
| Redis | `libs/redis/client.py`, `libs/redis/idempotency.py`, `src/infrastructure/cache/redis_*_adapter.py` |
| Queue | `libs/queue/client.py`, `apps/support/*/services/sqs_queue.py` |
| 배포 | `scripts/full_redeploy.ps1`, `scripts/redeploy_worker_asg.ps1`, `scripts/setup_elasticache_redis.ps1` |
| 인프라 | `infra/worker_asg/` |

