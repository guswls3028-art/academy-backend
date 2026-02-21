# ENTERPRISE STABILIZATION PATCH — VIDEO JOB SYSTEM (REVISED)

Job System 마이그레이션 이후 운영 안정성을 위한 보강 패치.

---

## 1. BacklogCount 정의

**정의**: `Job.state IN (QUEUED, RETRY_WAIT)` (RUNNING 제외)

- RUNNING은 backlog가 아님.
- TargetTracking의 Positive Feedback Loop 방지 위해 RUNNING 포함 금지.
- Scale-in 보호: ScaleInCooldown=300s (인프라 설정).

| 파일 | 변경 |
|------|------|
| `repositories_video.py` | `job_count_backlog()`에서 RUNNING 제외 |

---

## 2. Heartbeat Lease 연장

**변경**: `job_heartbeat(job_id)` 호출 시 `last_heartbeat_at` 뿐 아니라 `locked_until = now() + lease_seconds`도 함께 갱신.

| 파일 | 변경 |
|------|------|
| `repositories_video.py` | `job_heartbeat(job_id, lease_seconds=3600)` 시그니처 및 구현 |
| `sqs_main.py` | `job_heartbeat(job_id, lease_seconds=VISIBILITY_EXTEND_SECONDS)` 호출 |

---

## 3. cancel_requested 처리 (REVISED)

**동작**: Worker Heartbeat Thread에서 60초마다 ChangeMessageVisibility + job_heartbeat 수행 **후** DB에서 cancel_requested 확인. True이면:
1. 현재 실행 중인 ffmpeg subprocess에 SIGTERM 전달
2. CancelledError 발생
3. Job.state=CANCELLED 처리

| 파일 | 변경 |
|------|------|
| `models.py` | VideoTranscodeJob에 `cancel_requested` 필드 |
| `repositories_video.py` | `job_set_cancel_requested`, `job_is_cancel_requested` |
| `video_views.py` | retry API: RUNNING Job에 `job_set_cancel_requested(cur.id)` |
| `sqs_main.py` | Heartbeat loop: job_heartbeat 후 `job_is_cancel_requested` → ffmpeg SIGTERM → `cancel_event.set()` |
| `current_transcode.py` | ffmpeg process 등록/해제 (`set_current`, `clear_current`, `get_current`) |
| `transcoder.py` | `job_id`, `cancel_event` 전달, `set_current`/`clear_current`, cancel 시 CancelledError |
| `processor.py` | `transcode_to_hls`에 `job_id`, `cancel_event` 전달 |

---

## 4. DLQ State Sync Lambda (REVISED)

**목적**: DLQ는 retry가 아닌 **state reconciliation**.

**조건**: `Job.state NOT IN (SUCCEEDED, DEAD)` 일 때만 `job_mark_dead(job_id)` 호출.

| 파일 | 변경 |
|------|------|
| `internal_views.py` | VideoDlqMarkDeadView: job 조회 후 state 검사, SUCCEEDED/DEAD이면 skip |
| `video_dlq_poller_lambda` | DLQ poll → job_id 추출 → API 호출 |

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
