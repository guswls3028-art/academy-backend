# VIDEO UPLOAD → SQS ENQUEUE 실행 경로 (STRICT TRACE)

## 1. 실행 경로 요약

```
POST /api/.../videos/{pk}/upload/complete/
  → VideoViewSet.upload_complete()     [video_views.py]
  → [조건 통과 시] VideoSQSQueue().enqueue(video)   [sqs_queue.py]
  → queue_client.send_message()       [libs/queue/client.py → SQS boto3]
```

## 2. 정확한 코드 경로

| 단계 | 파일 | 함수/위치 | 설명 |
|------|------|-----------|------|
| 1 | `apps/support/video/views/video_views.py` | `VideoViewSet.upload_complete()` | 업로드 완료 API 핸들러 |
| 2 | 동일 | `video.status = UPLOADED` 후 `video.save()` | DB 상태 저장 |
| 3 | `apps/support/video/services/sqs_queue.py` | `VideoSQSQueue.enqueue(video)` | **enqueue 호출** (enqueue_video_job에 해당) |
| 4 | 동일 | `self.queue_client.send_message(...)` | SQS 메시지 전송 |
| 5 | `libs/queue/client.py` | `SQSQueueClient.send_message()` | boto3 sqs.send_message 호출 |

**참고**: `enqueue_video_job`라는 함수명은 없음. `VideoSQSQueue().enqueue(video)`가 동일 역할.

## 3. upload_complete 분기 (enqueue 호출 위치)

- **분기 A** (ffmpeg_module_missing): `reason == "ffmpeg_module_missing"` → UPLOADED 저장 → enqueue
- **분기 B** (duration<min): `duration < VIDEO_MIN_DURATION_SECONDS` → UPLOADED 저장 → enqueue
- **분기 C** (정상): ffprobe 통과 → UPLOADED 저장 → enqueue

## 4. enqueue가 호출되지 않는 조건

| 조건 | 위치 | 결과 |
|------|------|------|
| `video.status != PENDING` | upload_complete 314행 | 409 Conflict 반환, enqueue 미호출 |
| `head_object` 실패 (객체 없음/비어있음) | upload_complete 320행 | 409, enqueue 미호출 |
| `create_presigned_get_url` 예외 | upload_complete 329행 | 409, enqueue 미호출 |
| `_validate_source_media_via_ffprobe` 실패 (no_video_stream, duration_missing 등) | upload_complete 336행 | 409 미반환 but **ok=False** → **아래 조건문에서 early return 없음** → 분기 C로 진행 가능? |
| ffprobe 실패 (ok=False, reason != ffmpeg_module_missing) | 336행 | `duration`이 meta에 없을 수 있음 → `duration < min_dur` 또는 None 비교 |

**중요**: `ok=False` 이고 `reason != "ffmpeg_module_missing"` 이면:
- `duration = meta.get("duration")` → None 가능
- `duration is not None and duration < min_dur` → False (duration이 None이면)
- `video.duration = duration` (None), `video.status = UPLOADED`, save
- **분기 C와 동일 경로로 enqueue 호출됨** (353~383행)

`ok=False` 이고 `reason == "duration_missing"` 등이면 `duration`이 None. 그 경우:
- `duration < min_dur` 조건은 `duration is not None` 이므로 False
- 분기 B 스킵
- 371~383행 (분기 C) 실행 → enqueue 호출

즉 **ffprobe가 실패해도** (duration_missing, no_video_stream 제외) **대부분 enqueue는 호출됨**.

**enqueue가 호출되지 않는 유일한 early exit**:
- status != PENDING
- head_object 실패
- presigned_get_url 예외

## 5. enqueue 내부에서 실패하는 조건

| 조건 | sqs_queue.py | 동작 |
|------|--------------|------|
| `video.status != UPLOADED` | 73행 | False 반환, warning 로그 |
| `video.session.lecture.tenant` 예외 | 83행 | False 반환, error 로그 |
| `send_message` 예외 | 119행 | logger.exception으로 traceback 출력, False 반환 |
| `send_message` 반환값 False | 104행 | error 로그, False 반환 |

## 6. TRACE 로그 마커 (임시)

로그 grep: `VIDEO_UPLOAD_TRACE`

| execution 마커 | 의미 |
|----------------|------|
| 1_ENTRY | upload_complete 진입 |
| 2_BEFORE_ENQUEUE | Video 모델 UPLOADED 저장 완료, enqueue 호출 직전 |
| 3_ENQUEUE_ENTRY | enqueue() 진입 |
| 4_SEND_MESSAGE_CALL | send_message 호출 직전 |
| 5_SEND_MESSAGE_DONE | send_message 반환 직후 |
| ERR_ENQUEUE | enqueue 내 예외 발생 (exposed) |

## 7. sqs_queue.py 예외 처리

- `enqueue()` 내 try/except는 **예외를 삼키지 않음**. `logger.exception()`으로 전체 traceback 로그.
- `VIDEO_UPLOAD_TRACE | enqueue exception (exposed)` 로그로 오류 위치 확인 가능.

---

## 8. SQS QueueUrl runtime 검증 (SQS_QUEUE_URL_TRACE)

### 로그 마커

`libs/queue/client.py` `SQSQueueClient.send_message()` 실행 시:

```
SQS_QUEUE_URL_TRACE | send_message | queue_name=%s queue_url=%s region=%s tenant_id=%s
```

### 예상 QueueUrl

```
https://sqs.ap-northeast-2.amazonaws.com/809466760795/academy-video-jobs
```

### queue_name 출처 (video enqueue)

| 우선순위 | 출처 | 값 |
|----------|------|-----|
| 1 | 환경변수 `VIDEO_SQS_QUEUE_NAME` | .env 또는 실행 시 export |
| 2 | 기본값 | `academy-video-jobs` |

**코드 경로**:
```
apps/support/video/services/sqs_queue.py
  → _get_queue_name() = getattr(settings, "VIDEO_SQS_QUEUE_NAME", self.QUEUE_NAME)
  → self.QUEUE_NAME = "academy-video-jobs"

apps/api/config/settings/base.py
  → VIDEO_SQS_QUEUE_NAME = os.getenv("VIDEO_SQS_QUEUE_NAME", "academy-video-jobs")
```

### tenant/Program/ui_config 기반 오버라이드 여부

- **없음**. Video SQS 큐 이름은 `settings.VIDEO_SQS_QUEUE_NAME`(env 기반)만 사용.
- tenant config, Program.ui_config, 테넌트별 큐 분리는 **사용하지 않음**.
