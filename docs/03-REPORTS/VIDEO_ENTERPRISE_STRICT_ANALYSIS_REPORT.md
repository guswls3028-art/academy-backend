# Video Worker 엔터프라이즈 정석 구현 — 코드 분석 보고서

**작성일**: 2026-02-21  
**근거**: `grep` 및 실제 파일/라인 기반

---

## 0. 현재 문제 (확정)

| 문제 | 근거 |
|------|------|
| VIDEO_FAST_ACK=1 시 receive 직후 DeleteMessage | `sqs_main.py` L258-259: `if VIDEO_FAST_ACK: queue.delete_message(receipt_handle)` — handler 호출 전 |
| 워커 종료 시 job 유실 | 메시지 이미 삭제됨 → SQS 재전달 불가 → DB=PROCESSING stuck |
| 재인코딩 API | `video_views.py` L413-441 `retry()` — READY/FAILED만 허용, UPLOADED→SQS enqueue |

---

## 1. Video Worker 처리 흐름 (파일·라인 기준)

### 1.1 Entry Point

| 파일 | 라인 | 내용 |
|------|------|------|
| `apps/worker/video_worker/sqs_main.py` | 150 | `message = queue.receive_message(wait_time_seconds=SQS_WAIT_TIME_SECONDS)` |
| `apps/worker/video_worker/sqs_main.py` | 258-259 | `if VIDEO_FAST_ACK: queue.delete_message(receipt_handle)` — **handler 전** |
| `apps/worker/video_worker/sqs_main.py` | 296-297 / 332-333 | `result = handler.handle(job, cfg)` |
| `apps/worker/video_worker/sqs_main.py` | 380 | `if not VIDEO_FAST_ACK: queue.delete_message(receipt_handle)` — result=="ok" 시 |

### 1.2 Handler 내부 (ProcessVideoJobHandler)

| 파일 | 라인 | 내용 |
|------|------|------|
| `src/application/video/handler.py` | 74-80 | `is_cancel_requested` 체크 → skip:cancel |
| `src/application/video/handler.py` | 95-109 | **Fast ACK**: `try_claim_video` → 실패 시 `try_reclaim_video` + enqueue |
| `src/application/video/handler.py` | 112-119 | **Legacy**: `idempotency.acquire_lock` → `mark_processing` |
| `src/application/video/handler.py` | 123-124 | `hls_path, duration = self._process_fn(job, cfg, progress)` — ffmpeg 실행 |
| `src/application/video/handler.py` | 126-132 | `complete_video` / `fail_video` |

### 1.3 Visibility / Heartbeat

| 파일 | 라인 | 내용 |
|------|------|------|
| `sqs_main.py` | 48-49 | `VISIBILITY_EXTEND_SECONDS=900`, `VISIBILITY_EXTEND_INTERVAL_SECONDS=90` |
| `sqs_main.py` | 66-76 | `_visibility_extender_loop`: 90초마다 `change_message_visibility(900)` |
| `sqs_main.py` | 80-89 | `_heartbeat_loop`: 20초마다 `set_video_heartbeat(ttl=60)` (Redis) |
| `sqs_main.py` | 318-323 | **VIDEO_FAST_ACK=0** 일 때만 visibility extender 시작 |
| `sqs_main.py` | 258-259 | **VIDEO_FAST_ACK=1** 일 때 extender 미시작 (이미 delete됨) |

### 1.4 DeleteMessage 호출 위치

| 조건 | 파일:라인 | 시점 |
|------|-----------|------|
| VIDEO_FAST_ACK=1 | sqs_main.py:258 | **receive 직후, handler 전** ❌ |
| result=="ok" & !VIDEO_FAST_ACK | sqs_main.py:380 | handler 성공 후 ✅ |
| result=="skip:cancel" & !VIDEO_FAST_ACK | sqs_main.py:408 | handler skip 후 ✅ |
| delete_r2 | sqs_main.py:191,210 | R2 삭제 완료 후 |
| READY 스킵 | sqs_main.py:222 | DB READY 확인 후 |
| Invalid message | sqs_main.py:228 | 검증 실패 시 |

---

## 2. 재인코딩 API (retry)

| 항목 | 값 |
|------|-----|
| 파일 | `apps/support/video/views/video_views.py` |
| 메서드 | `retry()` L413-441 |
| URL | `POST /media/videos/{id}/retry/` |
| 허용 상태 | READY, FAILED (L419-421) |
| 거부 | UPLOADED, PROCESSING (L417) |
| 동작 | `video.status = UPLOADED` → `save` → `VideoSQSQueue().enqueue(video)` |
| 원자성 | `@transaction.atomic` + enqueue 실패 시 `ValidationError` raise (rollback) |

### 2.1 Enqueue

| 파일 | 라인 | 내용 |
|------|------|------|
| `apps/support/video/services/sqs_queue.py` | 55-126 | `enqueue(video)` — status=UPLOADED 검증, `send_message` |
| `apps/support/video/services/sqs_queue.py` | 92-98 | message: `video_id`, `file_key`, `tenant_id`, `tenant_code`, `created_at`, `attempt: 1` |
| `apps/support/video/services/sqs_queue.py` | 39-42 | `QUEUE_NAME=academy-video-jobs`, `DLQ_NAME=academy-video-jobs-dlq`, `MAX_RECEIVE_COUNT=3` |

---

## 3. Stuck Recovery (reconcile_video_processing)

| 파일 | 라인 | 내용 |
|------|------|------|
| `apps/support/video/management/commands/reconcile_video_processing.py` | 48-53 | `Video.objects.filter(status=PROCESSING)` |
| L60-61 | `lease_expired = video.leased_until < now` / `no_heartbeat = not has_video_heartbeat(...)` |
| L79-94 | `try_reclaim_video` → `status=UPLOADED` → `queue.enqueue(video)` |

**전제**: `Video.leased_until`, `Video.leased_by` (models.py L121-122), Redis heartbeat (`redis_status_cache.py`).

VIDEO_FAST_ACK=1 시 `try_claim_video` 사용 → `leased_by`/`leased_until` 설정됨.  
Legacy(VIDEO_FAST_ACK=0) 시 `mark_processing`만 사용 → `leased_until` 미설정. `reconcile`은 `leased_until` 또는 `no_heartbeat` 기준.

---

## 4. DB / Lock 구조

| 파일 | 라인 | 내용 |
|------|------|------|
| `apps/support/video/models.py` | 74-79 | `status`: PENDING, UPLOADED, PROCESSING, READY, FAILED |
| `apps/support/video/models.py` | 121-122 | `leased_until`, `leased_by` |
| `academy/adapters/db/django/repositories_video.py` | 430-461 | `mark_processing`: UPLOADED→PROCESSING |
| `academy/adapters/db/django/repositories_video.py` | 462-507 | `try_claim_video`: UPLOADED→PROCESSING + leased_by/leased_until |
| `academy/adapters/db/django/repositories_video.py` | 509-530 | `try_reclaim_video`: PROCESSING→UPLOADED (leased_until 만료 또는 force) |

**VideoTranscodeJob 테이블**: 없음. Video 모델에 `leased_by`, `leased_until`만 존재.

---

## 5. SQS / DLQ 설정

| 파일 | 라인 | 내용 |
|------|------|------|
| `scripts/create_sqs_resources.py` | 62-68 | `VisibilityTimeout=300`, `RedrivePolicy.maxReceiveCount=3` |
| `apps/support/video/services/sqs_queue.py` | 39-42 | `QUEUE_NAME`, `DLQ_NAME`, `MAX_RECEIVE_COUNT=3` |

---

## 6. VIDEO_FAST_ACK 사용 위치

| 파일 | 라인 | 내용 |
|------|------|------|
| `apps/worker/video_worker/sqs_main.py` | 63 | `VIDEO_FAST_ACK = os.environ.get("VIDEO_FAST_ACK", "0") == "1"` |
| `scripts/full_redeploy.ps1` | 57 | `-e VIDEO_FAST_ACK=1` (academy-video-worker) |
| `infra/worker_asg/user_data/video_worker_user_data.sh` | 38 | `-e VIDEO_FAST_ACK=1` |
| `docker-compose.yml` | 168 | `VIDEO_FAST_ACK: ${VIDEO_FAST_ACK:-1}` |

---

## 7. 결론 및 권장 패치

### 7.1 즉시 적용 (VIDEO_FAST_ACK 제거/0 고정)

1. **sqs_main.py**: `VIDEO_FAST_ACK` 분기 제거, delete_message는 handler 성공/취소 후에만 호출
2. **full_redeploy.ps1**, **video_worker_user_data.sh**, **docker-compose.yml**: `VIDEO_FAST_ACK=0` 또는 제거

### 7.2 이미 충족된 항목

- **Long job heartbeat**: `_visibility_extender_loop` 90초마다 900초 연장 (VIDEO_FAST_ACK=0)
- **Redis heartbeat**: `_heartbeat_loop` 20초마다 (VIDEO_FAST_ACK=1에서도 사용)
- **DB lease**: `try_claim_video` / `mark_processing`
- **Stuck scanner**: `reconcile_video_processing` (PROCESSING + lease/heartbeat 만료 → reclaim → enqueue)
- **DLQ**: `maxReceiveCount=3`, `academy-video-jobs-dlq`
- **재인코딩 API**: `retry` — DB UPLOADED + enqueue (원자적)

### 7.3 미구현/확장 필요

- **VideoTranscodeJob 테이블**: 현재 없음 — Video.leased_by/leased_until로 대체 가능. 필요 시 별도 모델 추가
- **로그 job_id**: `request_id` 있음 (L231). `job_id` 용어 통일 권장
- **CloudWatch 메트릭**: B1 backlog-count 외 별도 success/failure/latency 훅은 없음

---

## 8. 다음 단계

1. VIDEO_FAST_ACK=0 고정 패치 적용
2. 배포 스크립트에서 VIDEO_FAST_ACK=1 제거
3. 검증: 워커 강제 종료 후 visibility timeout 경과 → 메시지 재전달 → 재처리 확인
