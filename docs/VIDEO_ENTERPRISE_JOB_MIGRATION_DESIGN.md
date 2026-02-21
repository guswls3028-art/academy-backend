# Video Transcode Pipeline — Job 기반 마이그레이션 설계

> grep 기반 현황 정리 + VideoTranscodeJob 도입 설계

---

## 1. Video.status 기반 로직 사용 지점 (grep 근거)

### 1.1 실행 상태(Processing) 의존

| 파일 | 라인 | 용도 |
|------|------|------|
| `repositories_video.py` | 439-447 | `mark_processing`: UPLOADED→PROCESSING |
| `repositories_video.py` | 479-491 | `try_claim_video`: UPLOADED→PROCESSING |
| `repositories_video.py` | 521-525 | `try_reclaim_video`: PROCESSING→UPLOADED |
| `repositories_video.py` | 553-563 | `complete_video`: PROCESSING→READY |
| `repositories_video.py` | 594-596 | `fail_video`: PROCESSING→FAILED |
| `sqs_queue.py` | 321-335, 399-402, 462-474 | complete_video, fail_video, mark_processing (Video.status 기준) |
| `reconcile_video_processing.py` | 51, 89 | `filter(status=PROCESSING)` |
| `internal_views.py` | 53, 59 | processing-complete: READY 체크, status=READY 저장 |
| `internal_views.py` | 93 | backlog-count: `status__in=[UPLOADED, PROCESSING]` |

### 1.2 결과 상태(READY/FAILED) 의존

| 파일 | 라인 | 용도 |
|------|------|------|
| `video_views.py` | 325, 353, 374, 391 | upload_complete: PENDING 검증, UPLOADED 저장 |
| `video_views.py` | 417-424 | retry: UPLOADED/PROCESSING 거부, status→UPLOADED |
| `playback_mixin.py` | 40 | `video.status != READY` 시 재생 거부 |
| `serializers.py` | 113-173, 223 | `obj.status != PROCESSING` 분기, `obj.Status.READY` |
| `progress_views.py` | 50, 76, 95 | status READY/FAILED/PROCESSING 분기 |
| `student_app/media/views.py` | 99, 513 | `video.status != READY` 시 접근 거부 |
| `sqs_main.py` | 227 | `get_video_status(video_id) == "READY"` → 메시지 skip |

### 1.3 BacklogCount (B1 TargetTracking)

| 파일 | 라인 | 내용 |
|------|------|------|
| `internal_views.py` | 92-95 | `Video.objects.filter(status__in=[UPLOADED, PROCESSING]).count()` |
| `lambda_function.py` | 104-106 | API로 backlog 조회, DB SSOT |

---

## 2. VideoTranscodeJob 도입 설계

### 2.1 아키텍처 원칙

- **Video**: Resource. `status`는 결과만: UPLOADED / READY / FAILED (PENDING은 업로드 플로우 유지)
- **VideoTranscodeJob**: Execution. `state`가 실행 상태 관리.

### 2.2 VideoTranscodeJob 모델

```python
# apps/support/video/models.py

import uuid

class VideoTranscodeJob(models.Model):
    class State(models.TextChoices):
        QUEUED = "QUEUED", "대기"
        RUNNING = "RUNNING", "실행중"
        SUCCEEDED = "SUCCEEDED", "완료"
        FAILED = "FAILED", "실패"
        RETRY_WAIT = "RETRY_WAIT", "재시도대기"
        DEAD = "DEAD", "격리"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    video = models.ForeignKey(Video, on_delete=models.CASCADE, related_name="transcode_jobs")
    tenant_id = models.PositiveIntegerField(db_index=True)  # denormalized for queries

    state = models.CharField(max_length=20, choices=State.choices, default=State.QUEUED, db_index=True)
    attempt_count = models.PositiveIntegerField(default=1)
    locked_by = models.CharField(max_length=64, blank=True)
    locked_until = models.DateTimeField(null=True, blank=True)
    last_heartbeat_at = models.DateTimeField(null=True, blank=True)

    error_code = models.CharField(max_length=64, blank=True)
    error_message = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["state", "updated_at"]),
            models.Index(fields=["tenant_id", "state"]),
        ]
```

### 2.3 Video 모델 변경

```python
# Video 모델에 추가
current_job = models.ForeignKey(
    "VideoTranscodeJob",
    on_delete=models.SET_NULL,
    null=True,
    blank=True,
    related_name="+",
)
```

- `Video.Status`: PENDING, UPLOADED, READY, FAILED 유지.
- PROCESSING 의미: `current_job` 존재 + `current_job.state in (QUEUED, RUNNING, RETRY_WAIT)` 로 유도.

### 2.4 BacklogCount 변경

- **현재**: `Video.status IN (UPLOADED, PROCESSING)`
- **변경**: `VideoTranscodeJob.objects.filter(state__in=[QUEUED, RETRY_WAIT, RUNNING]).count()`

---

## 3. 메시지 구조 변경

### 3.1 enqueue (신규)

```python
message = {
    "job_id": str(job.id),  # UUID
    "video_id": int(video.id),
    "tenant_id": int(tenant_id),
    "file_key": str(video.file_key or ""),
}
```

### 3.2 Worker 수신 시

- `job_id` 필수. 없으면 invalid message 처리.
- `job_id`로 VideoTranscodeJob 조회.

---

## 4. Worker 처리 로직 (개요)

1. `receive_message` → `job_id` 검증
2. `Job.state IN (QUEUED, RETRY_WAIT)` → `RUNNING` 원자 전환 (UPDATE ... WHERE id=? AND state IN (...))
3. rowcount=0 → skip, visibility NACK
4. 60초마다: ChangeMessageVisibility + `last_heartbeat_at` 갱신
5. 성공: 결과 commit → Video.status=READY, Job.state=SUCCEEDED → DeleteMessage
6. 실패: attempt_count++, Job.state=RETRY_WAIT, visibility 유지

---

## 5. 변경 파일 목록 (예정)

| 파일 | 변경 유형 |
|------|-----------|
| `apps/support/video/models.py` | VideoTranscodeJob 추가, Video.current_job 추가 |
| `apps/support/video/migrations/0003_*.py` | 신규 마이그레이션 |
| `apps/support/video/services/sqs_queue.py` | enqueue(job_id 포함), enqueue_by_job() |
| `academy/adapters/db/django/repositories_video.py` | Job 기반 claim/complete/fail, mark_processing 제거 |
| `src/application/video/handler.py` | Job 기반 처리로 전면 교체 |
| `apps/worker/video_worker/sqs_main.py` | job_id 기반, VIDEO_FAST_ACK 제거 |
| `apps/support/video/views/video_views.py` | retry: Job 생성 + enqueue |
| `apps/support/video/views/internal_views.py` | BacklogCount: Job.state 기준 |
| `apps/support/video/management/commands/` | reconcile → stuck scanner (Job 기반) |
| `apps/support/video/serializers.py` | processing 유도: current_job.state |
| `apps/support/video/views/playback_mixin.py` | READY 검증 유지 |
| `apps/support/video/views/progress_views.py` | Redis/Job 조합 유지 |
