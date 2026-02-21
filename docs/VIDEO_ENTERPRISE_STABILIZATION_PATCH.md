# ENTERPRISE STABILIZATION PATCH — VIDEO JOB SYSTEM

Job System 마이그레이션 이후 운영 안정성을 위한 보강 패치.

---

## 1. BacklogCount 수정

**변경**: `Job.state IN (QUEUED, RETRY_WAIT, RUNNING)` → `Job.state IN (QUEUED, RETRY_WAIT)`

RUNNING은 이미 워커가 처리 중이므로 backlog로 집계하지 않음.

| 파일 | 변경 |
|------|------|
| `repositories_video.py` | `job_count_backlog()`에서 RUNNING 제외 |
| `internal_views.py` | docstring 수정 |

---

## 2. Heartbeat Lease 연장

**변경**: `job_heartbeat(job_id)` 호출 시 `last_heartbeat_at` 뿐 아니라 `locked_until = now() + lease_seconds`도 함께 갱신.

| 파일 | 변경 |
|------|------|
| `repositories_video.py` | `job_heartbeat(job_id, lease_seconds=3600)` 시그니처 및 구현 |
| `sqs_main.py` | `job_heartbeat(job_id, lease_seconds=VISIBILITY_EXTEND_SECONDS)` 호출 |

---

## 3. cancel_requested 도입

**변경**: Redis 기반 `set_cancel_requested` → VideoTranscodeJob.cancel_requested BOOLEAN.

| 파일 | 변경 |
|------|------|
| `models.py` | VideoTranscodeJob에 `cancel_requested` 필드 추가 |
| `migrations/0004_*.py` | 마이그레이션 |
| `repositories_video.py` | `job_set_cancel_requested(job_id)`, `job_is_cancel_requested(job_id)` 추가 |
| `video_views.py` | retry API: RUNNING Job에 `job_set_cancel_requested(cur.id)` 설정 후 새 Job 생성 |
| `sqs_main.py` | `_cancel_check()` → `job_is_cancel_requested(job_id)` 사용 |

**retry API 동작**:
- QUEUED/RETRY_WAIT: "Already in backlog" → 재시도 불가
- RUNNING: cancel_requested=True 설정, 새 Job 생성 및 enqueue (협력적 취소)

---

## 4. DLQ Poller

**신규**: academy-video-jobs-dlq를 EventBridge rate(2 min) Lambda로 poll.

| 파일 | 설명 |
|------|------|
| `infra/worker_asg/video_dlq_poller_lambda/lambda_function.py` | DLQ 메시지 수신 → job_id 추출 → POST `/api/v1/internal/video/dlq-mark-dead/` → DeleteMessage |
| `internal_views.py` | VideoDlqMarkDeadView (POST, body: `{"job_id": "uuid"}`) |
| `urls.py` | `/internal/video/dlq-mark-dead/` |

**환경 변수**: VIDEO_BACKLOG_API_URL, LAMBDA_INTERNAL_API_KEY, VIDEO_DLQ(기본: academy-video-jobs-dlq)

---

## 5. scan_stuck_video_jobs Lambda 전환

**변경**: Django cron 제거 → EventBridge rate(2 min) Lambda로 전환. Worker ASG lifecycle과 독립.

| 파일 | 설명 |
|------|------|
| `infra/worker_asg/video_scan_stuck_lambda/lambda_function.py` | POST `/api/v1/internal/video/scan-stuck/` 호출 |
| `internal_views.py` | VideoScanStuckView (POST, body: `{"threshold": 3}`) |

**Django management command**: `scan_stuck_video_jobs`는 유지 (수동 실행/테스트용).

---

## 6. BacklogScore Metric

**변경**: BacklogCount → BacklogScore.

```
BacklogScore = SUM(
  CASE
    WHEN state='QUEUED' THEN 1
    WHEN state='RETRY_WAIT' THEN 2
  END
)
```

| 파일 | 변경 |
|------|------|
| `repositories_video.py` | `job_compute_backlog_score()` 추가 |
| `internal_views.py` | VideoBacklogScoreView (GET `/internal/video/backlog-score/`) |
| `queue_depth_lambda/lambda_function.py` | `_fetch_video_backlog_score_from_api()` 호출, CloudWatch `BacklogScore` 퍼블리시 |

**주의**: ASG TargetTracking 정책에서 기존 `BacklogCount` → `BacklogScore`로 metric 교체 필요.

---

## 배포 순서

1. **마이그레이션**: `python manage.py migrate video`
2. **API/Worker**: 동시 배포
3. **Lambda 배포**: video_dlq_poller_lambda, video_scan_stuck_lambda
4. **EventBridge 규칙**: 2분 rate로 Lambda 트리거
5. **ASG 정책**: BacklogCount → BacklogScore metric 변경
