# FINAL HARDENING PATCH (NO GUESSING)

REVISED 패치 반영 후 엔터프라이즈 보강. 모든 변경은 파일/라인 근거와 함께 기술.

---

## 1) BacklogScore 전환 보류 — TargetTracking = BacklogCount 고정

**요구**: TargetTracking metric은 BacklogCount(QUEUED+RETRY_WAIT)로 고정. 안정화 검증 후에만 Score/가중치 도입 검토.

### 변경

| 파일 | 라인 | 변경 내용 |
|------|------|-----------|
| `infra/worker_asg/queue_depth_lambda/lambda_function.py` | 92–106 (삭제) | `_fetch_video_backlog_score_from_api()` 함수 제거 |
| `infra/worker_asg/queue_depth_lambda/lambda_function.py` | 121–126 | `video_backlog_score` → `video_backlog`, `_fetch_video_backlog_from_api()` 사용, fallback 정수화 |
| `infra/worker_asg/queue_depth_lambda/lambda_function.py` | 154–168 | CloudWatch 퍼블리시: `MetricName`: "BacklogScore" → "BacklogCount", `Value`: `video_backlog` (int) |
| `infra/worker_asg/queue_depth_lambda/lambda_function.py` | 169–178 | 로그/반환: `backlog_score` → `backlog` / `video_backlog_count` |

**결과**: queue_depth_lambda는 `/api/v1/internal/video/backlog-count/`만 호출하며, Academy/VideoProcessing 네임스페이스에 **BacklogCount** 메트릭만 퍼블리시.

---

## 2) DLQ poller state 조건 강화 (scan_stuck와 경합 방지)

**요구**:
- `Job.state in (QUEUED, RETRY_WAIT)`: `job_mark_dead(job_id)`
- `Job.state == RUNNING`: DEAD로 바꾸지 않음, **alert/log only**
- `state in (SUCCEEDED, DEAD)`: ignore

### 변경

| 파일 | 라인 | 변경 내용 |
|------|------|-----------|
| `apps/support/video/views/internal_views.py` | 113–140 | `VideoDlqMarkDeadView.post`: state별 분기 추가 |

**분기 로직 (internal_views.py)**:
1. `job.state in (SUCCEEDED, DEAD)` → `Response({"ok": True, "skipped": "already_terminal", "state": job.state})`
2. `job.state == RUNNING` → `logger.warning("DLQ_RUNNING_ALERT | job_id=... state=RUNNING — not marking DEAD (scan_stuck may recover)")` 후 `Response({"ok": True, "skipped": "running_alert_only", "state": job.state})`
3. `job.state in (QUEUED, RETRY_WAIT)` → `job_mark_dead(job_id)` 호출 후 성공/실패 응답
4. 그 외 (CANCELLED, FAILED 등) → `job_mark_dead(job_id)` 호출

**결과**: RUNNING은 scan_stuck이 처리하도록 두고, DLQ poller는 QUEUED/RETRY_WAIT만 DEAD로 정리. race/경합 회피.

---

## 3) cancel_requested kill 로직 강화

**요구**:
- `current_transcode.get_current()`로 얻은 `(process, job_id)`가 **현재 처리 job_id와 일치할 때만** terminate
- terminate 후 `wait(timeout=10~30s)`, timeout이면 `kill()` + `wait()`
- 이후 `cancel_event.set()`로 CancelledError 유도

### 변경

| 파일 | 라인 | 변경 내용 |
|------|------|-----------|
| `apps/worker/video_worker/sqs_main.py` | 20 (추가) | `import subprocess` |
| `apps/worker/video_worker/sqs_main.py` | 112–128 | Heartbeat 루프 내 cancel_requested 처리 블록 수정 |

**로직 (sqs_main.py _job_visibility_and_heartbeat_loop)**:
1. `job_is_cancel_requested(job_id)` 확인
2. `process, proc_job_id, _ = get_current()` — **proc_job_id == job_id**이고 process가 살아 있을 때만 진행
3. `process.terminate()`
4. `process.wait(timeout=15)` (10~30s 범위에서 15s 사용)
5. `subprocess.TimeoutExpired` 시 `process.kill()` 후 `process.wait()`
6. `cancel_event.set()` 호출
7. 예외 시에도 `cancel_event.set()` 호출하여 CancelledError 유도

**결과**: job_id 일치 시에만 ffmpeg를 종료하고, terminate 후 15초 대기, 미종료 시 kill 후 cancel_event로 CancelledError 유도.
