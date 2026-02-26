# Video Worker SQS 인코딩 파이프라인 — 현 구조 리포트 (grep 기반)

> **규칙**: 모든 진술은 grep 결과 + 파일 경로 + 라인 번호로 근거함. 추측 없음.

---

## 1. 엔트리포인트 및 핵심 파일

| 항목 | 파일 | 라인 | 근거 |
|------|------|------|------|
| **Worker 진입점** | `apps/worker/video_worker/sqs_main.py` | — | `main()` 함수 |
| **Docker CMD** | `docker/video-worker/Dockerfile` | L37 | `CMD ["python", "-m", "apps.worker.video_worker.sqs_main"]` |
| **SQS 어댑터** | `src/infrastructure/video/sqs_adapter.py` | L12 | `class VideoSQSAdapter` — `VideoSQSQueue` 래핑 |
| **SQS 큐 구현** | `apps/support/video/services/sqs_queue.py` | L24 | `class VideoSQSQueue` |
| **Handler** | `src/application/video/handler.py` | L34 | `class ProcessVideoJobHandler` |
| **Processor (ffmpeg)** | `src/infrastructure/video/processor.py` | — | `process_video` (sqs_main L27에서 import) |

---

## 2. SQS 수신 / 삭제 흐름 (수신 → 삭제 시점)

### 2.1 receive_message

| 파일 | 라인 | 내용 |
|------|------|------|
| `sqs_main.py` | 150 | `message = queue.receive_message(wait_time_seconds=SQS_WAIT_TIME_SECONDS)` |
| `sqs_queue.py` | 168 | `self.queue_client.receive_message(queue_name=..., wait_time_seconds=...)` |

### 2.2 DeleteMessage 호출 위치 (전수)

| 조건 | 파일 | 라인 | 시점 |
|------|------|------|------|
| **VIDEO_FAST_ACK=1** | `sqs_main.py` | **258-259** | **receive 직후, handler 호출 전** — 메시지 유실 위험 |
| delete_r2 성공 | `sqs_main.py` | 191 | R2 삭제 완료 후 |
| delete_r2 lock 스킵 | `sqs_main.py` | 210 | idempotency lock 실패 시 |
| invalid message (video_id 없음) | `sqs_main.py` | 222 | 파싱 실패 시 |
| VIDEO_ALREADY_READY_SKIP | `sqs_main.py` | 228 | get_video_status == READY 시 |
| **result=="ok"** (VIDEO_FAST_ACK=0) | `sqs_main.py` | 379-380 | handler 성공 + raw 삭제 후 |
| **result=="skip:cancel"** (VIDEO_FAST_ACK=0) | `sqs_main.py` | 407-408 | 취소 요청 스킵 후 |

### 2.3 VIDEO_FAST_ACK 관련 코드

| 파일 | 라인 | 내용 |
|------|------|------|
| `sqs_main.py` | 63 | `VIDEO_FAST_ACK = os.environ.get("VIDEO_FAST_ACK", "0") == "1"` |
| `sqs_main.py` | 258-259 | `if VIDEO_FAST_ACK: queue.delete_message(receipt_handle)` |
| `sqs_main.py` | 284-287 | VIDEO_FAST_ACK 시 visibility extender **미시작** |
| `sqs_main.py` | 318-323 | VIDEO_FAST_ACK=0일 때만 `_visibility_extender_loop` 시작 |
| `scripts/full_redeploy.ps1` | 57 | `-e VIDEO_FAST_ACK=1` (academy-video-worker) |
| `infra/worker_asg/user_data/video_worker_user_data.sh` | 38 | `-e VIDEO_FAST_ACK=1` |
| `docker-compose.yml` | 168 | `VIDEO_FAST_ACK: ${VIDEO_FAST_ACK:-1}` (기본값 1) |

---

## 3. Visibility Timeout / Heartbeat

### 3.1 Visibility Extender (ChangeMessageVisibility)

| 파일 | 라인 | 내용 |
|------|------|------|
| `sqs_main.py` | 46-48 | `VISIBILITY_EXTEND_SECONDS = 900`, `VISIBILITY_EXTEND_INTERVAL_SECONDS = 90` |
| `sqs_main.py` | 65-76 | `_visibility_extender_loop`: 90초마다 900초 연장 |
| `sqs_main.py` | 318-323 | **VIDEO_FAST_ACK=0일 때만** extender 스레드 시작 |
| `sqs_main.py` | 284-287 | VIDEO_FAST_ACK=1이면 extender 미시작 (메시지 이미 delete됨) |

### 3.2 Redis Heartbeat

| 파일 | 라인 | 내용 |
|------|------|------|
| `sqs_main.py` | 79-89 | `_heartbeat_loop`: 20초마다 `set_video_heartbeat(tenant_id, video_id, ttl=60)` |
| `sqs_main.py` | 325-330 | VIDEO_FAST_ACK=0에서도 heartbeat 스레드 시작 |
| `sqs_main.py` | 289-315 | VIDEO_FAST_ACK=1에서도 heartbeat 스레드 시작 |

### 3.3 SQS 큐 Visibility 설정

| 파일 | 라인 | 내용 |
|------|------|------|
| `scripts/create_sqs_resources.py` | 63 | `VisibilityTimeout=300` (5분) |
| `scripts/create_sqs_resources.py` | 66-68 | `RedrivePolicy.maxReceiveCount=3` |

---

## 4. Handler → ffmpeg → DB 업데이트

### 4.1 Handler.handle() 호출

| 파일 | 라인 | 내용 |
|------|------|------|
| `sqs_main.py` | 279-283 | VIDEO_FAST_ACK: `result = handler.handle(job, cfg)` |
| `sqs_main.py` | 334-336 | !VIDEO_FAST_ACK: `result = handler.handle(job, cfg)` |

### 4.2 job 구조 (워커 → handler)

| 파일 | 라인 | 필드 |
|------|------|------|
| `sqs_main.py` | 279-285 | `job = {video_id, file_key, tenant_id, tenant_code}` |
| `sqs_main.py` | 284-285 | VIDEO_FAST_ACK 시 `job["_worker_id"] = f"{cfg.WORKER_ID}-{request_id}"` |

**중요**: SQS 메시지에 `job_id` 필드 없음. `enqueue` 시 `video_id`, `file_key`, `tenant_id`, `tenant_code`, `created_at`, `attempt`만 전송.

### 4.3 Handler 내부 — 락/claim

| 파일 | 라인 | 내용 |
|------|------|------|
| `handler.py` | 95-109 | VIDEO_FAST_ACK: `try_claim_video(video_id, worker_id)` — 실패 시 `try_reclaim_video` + enqueue → `"skip:claim"` |
| `handler.py` | 111-119 | Legacy: `idempotency.acquire_lock(job_id)` → `mark_processing` — 실패 시 `"skip:mark_processing"` / `"lock_fail"` |
| `handler.py` | 68 | `job_id = f"encode:{video_id}"` (video_id 기반 가상 job_id) |

### 4.4 DB 상태 전환

| 파일 | 라인 | 함수 | 동작 |
|------|------|------|------|
| `repositories_video.py` | 430-461 | `mark_processing` | UPLOADED → PROCESSING (leased_until 미설정) |
| `repositories_video.py` | 462-507 | `try_claim_video` | UPLOADED → PROCESSING + leased_by, leased_until |
| `repositories_video.py` | 508-529 | `try_reclaim_video` | PROCESSING → UPLOADED (lease 만료 시) |
| `repositories_video.py` | 531- | `complete_video` | PROCESSING → READY |
| `repositories_video.py` | — | `fail_video` | PROCESSING → FAILED |

### 4.5 result 분기별 SQS 처리 (sqs_main)

| result | delete_message | change_visibility |
|--------|----------------|-------------------|
| "ok" | L379 (VIDEO_FAST_ACK=0만) | — |
| "skip:cancel" | L407 (VIDEO_FAST_ACK=0만) | — |
| "skip:claim" | — (이미 delete됨) | — |
| "skip:lock" | — | NACK 60~120초 |
| "skip:mark_processing" | — | NACK 60~120초 |
| "lock_fail" | — | NACK 60~120초 |
| "skip" | — | NACK 60~120초 |
| "failed" | — | NACK 180초 (VIDEO_FAST_ACK=0만) |

---

## 5. DB 모델 (Video)

| 파일 | 라인 | 필드 |
|------|------|------|
| `apps/support/video/models.py` | 30-37 | `Video.Status`: PENDING, UPLOADED, PROCESSING, READY, FAILED |
| `apps/support/video/models.py` | 120-122 | `leased_until`, `leased_by`, `processing_started_at` |
| `apps/support/video/models.py` | 102 | `error_reason` |

**VideoTranscodeJob 테이블**: 없음 (grep 결과). job 상태는 Video.status + leased_by/leased_until로 관리.

---

## 6. 재인코딩(retry) API

### 6.1 서버 라우트

| 항목 | 파일 | 라인 |
|------|------|------|
| URL prefix | `apps/api/v1/urls.py` | 74 | `path("media/", include("apps.support.video.urls"))` |
| Router | `apps/support/video/urls.py` | 30 | `router.register(r"videos", VideoViewSet, basename="videos")` |
| Action | `apps/support/video/views/video_views.py` | 413-441 | `@action(detail=True, methods=["post"], url_path="retry") def retry(...)` |

**전체 URL**: `POST /api/v1/media/videos/{pk}/retry/`

### 6.2 retry() 로직

| 파일 | 라인 | 내용 |
|------|------|------|
| `video_views.py` | 416 | `video = Video.objects.select_for_update().get(pk=...)` |
| `video_views.py` | 417-418 | UPLOADED/PROCESSING → `raise ValidationError("Already in backlog")` |
| `video_views.py` | 420-421 | READY/FAILED 외 → `raise ValidationError("Cannot retry: status must be READY or FAILED")` |
| `video_views.py` | 424-425 | `video.status = Video.Status.UPLOADED` → `save()` |
| `video_views.py` | 427-430 | `VideoSQSQueue().enqueue(video)` — 실패 시 `raise ValidationError(...)` |
| `video_views.py` | 437-439 | `return Response({"detail": "Video reprocessing queued (SQS)"}, 202)` |

**retry는 job_id를 생성하지 않음.** video_id만 사용. enqueue 시 `job_id` 포함 메시지 없음.

### 6.3 enqueue 메시지 형식

| 파일 | 라인 | 필드 |
|------|------|------|
| `sqs_queue.py` | 90-97 | `{video_id, file_key, tenant_id, tenant_code, created_at, attempt: 1}` |
| `sqs_queue.py` | 72-79 | `video.status != UPLOADED` 이면 enqueue 거부 |

---

## 7. 재인코딩 버튼 (프론트엔드)

### 7.1 호출 위치

| 파일 | 라인 | 호출 |
|------|------|------|
| `VideoDetailPage.tsx` | 59 | `api.post(\`/media/videos/${videoId}/retry/\`)` |
| `VideoDetailPage.tsx` | 158-160 | `onRetry` 조건: `["FAILED", "PROCESSING", "UPLOADED"].includes(video.status)` |
| `VideoExplorerPage.tsx` | 257-258 | `retryVideo(payload.videoId)` |
| `VideoExplorerPage.tsx` | 413 | `retryVideoMutation.mutate({ videoId: v.id, ... })` |
| `SessionVideosTab.tsx` | 92 | `api.post(\`/media/videos/${id}/retry/\`)` |
| `AsyncStatusBar.tsx` | 223 | `api.post(\`/media/videos/${task.meta.jobId}/retry/\`)` (jobId = videoId) |
| `videos.ts` | 177-178 | `retryVideo(videoId) => api.post(\`/media/videos/${videoId}/retry/\`)` |

### 7.2 프론트/백엔드 불일치 (재인코딩 “먹통” 원인)

| 구분 | 허용 status | 근거 |
|------|-------------|------|
| **프론트** | FAILED, **PROCESSING**, **UPLOADED** | `VideoDetailPage.tsx` L158 |
| **백엔드** | **READY**, FAILED | `video_views.py` L417-421 |

- PROCESSING/UPLOADED 시 버튼 노출 → 클릭 시 `"Already in backlog"` 400 반환 → 사용자 입장에선 “먹통”
- READY 시 재인코딩(재처리)은 허용되나, 프론트는 `["FAILED","PROCESSING","UPLOADED"]`만 사용 → **READY일 때 retry 버튼 없음** (VideoDetailPage 기준)

---

## 8. Stuck Scanner (reconcile_video_processing)

| 파일 | 라인 | 내용 |
|------|------|------|
| `reconcile_video_processing.py` | 33 | `help = "Reclaim PROCESSING videos (lease expired or heartbeat missing) and re-enqueue"` |
| `reconcile_video_processing.py` | 49-54 | `get_video_queryset_with_relations().filter(status=PROCESSING)` |
| `reconcile_video_processing.py` | 60-61 | `lease_expired = video.leased_until is not None and video.leased_until < now` |
| `reconcile_video_processing.py` | 61 | `no_heartbeat = ... and not has_video_heartbeat(tenant_id, video.id)` |
| `reconcile_video_processing.py` | 79 | `repo.try_reclaim_video(video.id, force=force)` |
| `reconcile_video_processing.py` | 93-94 | `queue.enqueue(video)` |

**주의**: `mark_processing`만 사용하는 Legacy(VIDEO_FAST_ACK=0) 경로는 `leased_until`이 없음 → `lease_expired`가 항상 False → `no_heartbeat`에만 의존.

---

## 9. DLQ / Redrive

| 파일 | 라인 | 내용 |
|------|------|------|
| `create_sqs_resources.py` | 66-68 | `RedrivePolicy: deadLetterTargetArn, maxReceiveCount=3` |
| `sqs_queue.py` | 39-42 | `QUEUE_NAME=academy-video-jobs`, `DLQ_NAME=academy-video-jobs-dlq`, `MAX_RECEIVE_COUNT=3` |
| `sqs_queue.py` | 272-294 | `mark_failed` — 로그만, DLQ 전송 로직 없음 (SQS 자동 redrive에 의존) |

**DLQ 메시지**: SQS 기본 형식. `job_id` 필드는 원본 메시지에 없으므로 DLQ에도 없음.

---

## 10. SQS / 인프라 설정 요약

| 항목 | 값 | 근거 |
|------|-----|------|
| 큐 이름 | academy-video-jobs | `sqs_queue.py` L38, `create_sqs_resources.py` L56 |
| DLQ | academy-video-jobs-dlq | `create_sqs_resources.py` L25 |
| VisibilityTimeout | 300초 | `create_sqs_resources.py` L63 |
| maxReceiveCount | 3 | `create_sqs_resources.py` L68 |
| Extend 주기 | 90초마다 900초 | `sqs_main.py` L46-48, L71 |

---

## 11. 검증용 grep 명령

```bash
# VIDEO_FAST_ACK 사용 위치
grep -rn "VIDEO_FAST_ACK" C:\academy\apps C:\academy\scripts C:\academy\infra C:\academy\docker-compose.yml

# delete_message 호출
grep -n "delete_message" C:\academy\apps\worker\video_worker\sqs_main.py

# retry API
grep -n "retry" C:\academy\apps\support\video\views\video_views.py

# enqueue 메시지 형식
grep -A20 "def enqueue" C:\academy\apps\support\video\services\sqs_queue.py
```

---

## 12. 확정 사실 정리

1. **VIDEO_FAST_ACK=1** → receive 직후 delete → ffmpeg 중 워커 죽으면 SQS 메시지 유실, DB는 PROCESSING stuck.
2. **재인코딩 버튼** → 프론트: FAILED/PROCESSING/UPLOADED에서 노출, 백엔드: READY/FAILED만 허용 → PROCESSING/UPLOADED에서 “먹통”.
3. **job_id** → 메시지/DB에 없음. handler는 `job_id = f"encode:{video_id}"`로 가상 생성.
4. **VideoTranscodeJob 테이블** → 없음.
5. **Visibility extender** → VIDEO_FAST_ACK=0일 때만 동작. VIDEO_FAST_ACK=1이면 메시지가 이미 삭제되어 의미 없음.
6. **B1 스케일링** → BacklogCount 기반 TargetTracking으로 전환 완료. ASG desired 직접 제어 없음.
