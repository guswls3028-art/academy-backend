# Video Batch 설계 검증 — 정확 확인 보고서

추측 없이 **파일 경로 + 코드 스니펫** 기준으로 정리함.

---

## 1) VideoTranscodeJob 모델 정의 전체

**파일 경로:** `apps/support/video/models.py`

**필드 전체 + state enum:**

```python
# L160–211 (발췌)
class VideoTranscodeJob(models.Model):
    """
    Transcoding 실행 단위. Video는 Resource, Job은 Execution.
    SQS 메시지에 job_id 포함 → Worker는 job_id 기반으로 claim/처리.
    """

    class State(models.TextChoices):
        QUEUED = "QUEUED", "대기"
        RUNNING = "RUNNING", "실행중"
        SUCCEEDED = "SUCCEEDED", "완료"
        FAILED = "FAILED", "실패"
        RETRY_WAIT = "RETRY_WAIT", "재시도대기"
        DEAD = "DEAD", "격리"
        CANCELLED = "CANCELLED", "취소됨"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    video = models.ForeignKey(
        Video,
        on_delete=models.CASCADE,
        related_name="transcode_jobs",
    )
    tenant_id = models.PositiveIntegerField(db_index=True)

    state = models.CharField(
        max_length=20,
        choices=State.choices,
        default=State.QUEUED,
        db_index=True,
    )
    attempt_count = models.PositiveIntegerField(default=1)
    cancel_requested = models.BooleanField(default=False)
    locked_by = models.CharField(max_length=64, blank=True)
    locked_until = models.DateTimeField(null=True, blank=True)
    last_heartbeat_at = models.DateTimeField(null=True, blank=True)

    error_code = models.CharField(max_length=64, blank=True)
    error_message = models.TextField(blank=True)

    # AWS Batch 제출 추적 (디버깅/관측용)
    aws_batch_job_id = models.CharField(max_length=256, blank=True, db_index=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["state", "updated_at"]),
            models.Index(fields=["tenant_id", "state"]),
        ]
```

| 항목 | 결과 |
|------|------|
| **state enum** | `State(models.TextChoices)`: QUEUED, RUNNING, SUCCEEDED, FAILED, RETRY_WAIT, DEAD, CANCELLED |
| **aws_batch_job_id** | 존재. `models.CharField(max_length=256, blank=True, db_index=True)` (L196) |
| **tenant_id** | 존재. `models.PositiveIntegerField(db_index=True)` (L181), NOT null (필수) |
| **created_at / updated_at** | `auto_now_add=True` / `auto_now=True` (L199–200) |

---

## 2) submit_batch_job 함수 정의

**파일 경로:** `apps/support/video/services/batch_submit.py`

**반환값:**  
`tuple[Optional[str], Optional[str]]` — 성공 시 `(aws_job_id, None)`, 실패 시 `(None, error_message)`.

**코드 (핵심만):**

```python
# L18–31
def submit_batch_job(video_job_id: str) -> tuple[Optional[str], Optional[str]]:
    """
    ...
    Returns:
        (aws_job_id, None) 성공 시. (None, error_message) 실패 시.
        호출부에서 job.aws_batch_job_id 저장 또는 job.error_code/error_message 저장에 사용.
    """
```

```python
# L52–67
    try:
        client = boto3.client("batch", region_name=region)
        resp = client.submit_job(
            jobName=f"video-{video_job_id[:8]}",
            jobQueue=queue_name,
            jobDefinition=job_def_name,
            parameters={"job_id": str(video_job_id)},
            containerOverrides=container_overrides,
        )
        aws_job_id = resp.get("jobId")
        ...
        return (aws_job_id, None)
```

- **submit_batch_job 내부에는 DB 저장 코드 없음.** 반환값만 제공.
- **aws_batch_job_id를 DB에 저장하는 코드:** 호출부 `apps/support/video/services/video_encoding.py` L46–50:

```python
# video_encoding.py L46–50
    aws_job_id, submit_error = submit_batch_job(str(job.id))
    if aws_job_id:
        job.aws_batch_job_id = aws_job_id
        job.save(update_fields=["aws_batch_job_id", "updated_at"])
        return job
```

- **retryStrategy:** `batch_submit.py`의 `submit_job()` 호출에는 **retryStrategy 인자 없음.** Job Definition에만 있음 (아래 6번).

---

## 3) process_video 함수 정의

**파일 경로:** `src/infrastructure/video/processor.py`

- **내부에서 subprocess로 ffmpeg 실행하는지:**  
  **아니요.** `process_video`는 `transcode_to_hls()`를 호출하고, **ffmpeg 실행은 `apps/worker/video_worker/video/transcoder.py`의 `transcode_to_hls()`에서 수행.**

  `transcoder.py` L267–274 (duration 알 때):

  ```python
  p = subprocess.Popen(
      cmd,
      cwd=str(output_root.resolve()),
      stdout=subprocess.PIPE,
      stderr=subprocess.PIPE,
      text=True,
      bufsize=1,
  )
  ```

  즉, **ffmpeg는 `transcode_to_hls` 내부에서 `subprocess.Popen`으로 실행.**

- **blocking 구조인지 async인지:**  
  **Blocking.** `process_video()`는 동기 함수이며, `transcode_to_hls()`가 `p.wait(timeout=chunk)` 등으로 완료될 때까지 대기.

- **중간에 loop가 존재하는지:**  
  **예.**  
  - `processor.py`: 단계별로 `_check_abort(job)` 호출 반복 (presigning → downloading → probing → transcoding → validating → thumbnail → uploading).  
  - `transcoder.py`: `while True: ... p.wait(timeout=chunk) ...` 로 타임아웃 연장 루프 (L307–328, L364–377).

- **취소/중단 가능 구조인지:**  
  - **단계 사이:** `job["_cancel_check"]`가 호출되면 `_check_abort()`에서 `CancelledError` 발생 → 상위에서 처리.  
  - **batch_main에서:** `_cancel_check = lambda: job_is_cancel_requested(job_id) or _shutdown_event.is_set()` 이므로, SIGTERM 시 `_shutdown_event.set()` 후 다음 `_check_abort()`에서 취소 가능.  
  - **transcode_to_hls 내부:** `batch_main`은 `job_dict["_cancel_event"] = None`으로 넘기므로, transcoder의 `cancel_event.is_set()` 체크는 사용되지 않음.  
  - **컨테이너 SIGTERM:** `_handle_term` → `job_fail_retry` → `sys.exit(1)` 로 프로세스 종료 시 자식 ffmpeg도 함께 종료됨.

---

## 4) batch_main.py 전체 코드 원문

**파일 경로:** `apps/worker/video_worker/batch_main.py`

<details>
<summary>전체 코드 (펼치기)</summary>

```python
"""
Video Worker - AWS Batch 엔트리포인트

RUNNING 전환 + heartbeat + SIGTERM/SIGINT 처리로 scan_stuck 및 인프라 종료 대응.
State: QUEUED → RUNNING(job_set_running) → SUCCEEDED(job_complete) or RETRY_WAIT(job_fail_retry).
"""

from __future__ import annotations

import json
import logging
import os
import signal
import sys
import threading
import time

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "apps.api.config.settings.worker")
import django

django.setup()

from academy.adapters.db.django.repositories_video import (
    job_get_by_id,
    job_complete,
    job_fail_retry,
    job_heartbeat,
    job_mark_dead,
    job_is_cancel_requested,
    job_set_running,
)
from apps.worker.video_worker.config import load_config
from src.infrastructure.video.processor import process_video
from src.infrastructure.cache.redis_progress_adapter import RedisProgressAdapter
from apps.support.video.redis_status_cache import cache_video_status
from src.application.video.handler import CancelledError

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("video_worker_batch")

VIDEO_PROGRESS_TTL_SECONDS = int(os.getenv("VIDEO_PROGRESS_TTL_SECONDS", "14400"))
VIDEO_JOB_MAX_ATTEMPTS = int(os.environ.get("VIDEO_JOB_MAX_ATTEMPTS", "5"))
VIDEO_JOB_HEARTBEAT_SECONDS = int(os.environ.get("VIDEO_JOB_HEARTBEAT_SECONDS", "60"))

# SIGTERM/SIGINT 시 핸들러에서 사용할 현재 job_id (모듈 레벨)
_current_job_id: list[str | None] = [None]
_shutdown_event = threading.Event()
_heartbeat_stop = threading.Event()


def _handle_term(signum: int, frame: object) -> None:
    """SIGTERM/SIGINT 수신 시 DB에 종료 반영 후 즉시 종료 (Spot/scale-in/terminate-job 대응)."""
    _shutdown_event.set()
    jid = _current_job_id[0]
    if jid:
        try:
            job_fail_retry(jid, "TERMINATED")
            _log_json("BATCH_TERMINATED", job_id=jid, signal=signum)
        except Exception as e:
            logger.exception("job_fail_retry on signal failed: %s", e)
    sys.exit(1)


def _heartbeat_loop(job_id: str) -> None:
    """RUNNING job의 last_heartbeat_at을 주기 갱신 (scan_stuck 동작용)."""
    while not _heartbeat_stop.is_set():
        if _heartbeat_stop.wait(timeout=VIDEO_JOB_HEARTBEAT_SECONDS):
            break
        if _shutdown_event.is_set():
            break
        try:
            job_heartbeat(job_id, lease_seconds=VIDEO_JOB_HEARTBEAT_SECONDS * 2)
        except Exception as e:
            logger.debug("heartbeat failed: %s", e)


def _log_json(event: str, **kwargs) -> None:
    logger.info(json.dumps({"event": event, **kwargs}))


def _is_valid_uuid(s: str) -> bool:
    if not s or len(s) != 36:
        return False
    try:
        import uuid
        uuid.UUID(s)
        return True
    except (ValueError, TypeError):
        return False


def main() -> int:
    job_id = os.environ.get("VIDEO_JOB_ID") or (sys.argv[1] if len(sys.argv) > 1 else None)
    if not job_id:
        _log_json("BATCH_MAIN_ERROR", error="VIDEO_JOB_ID or argv[1] required")
        return 1

    _current_job_id[0] = job_id
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _handle_term)
    if hasattr(signal, "SIGINT"):
        signal.signal(signal.SIGINT, _handle_term)

    if not _is_valid_uuid(job_id):
        _log_json("JOB_NOT_FOUND", job_id=job_id, reason="not_a_uuid")
        return 0

    job_obj = job_get_by_id(job_id)
    if not job_obj:
        _log_json("JOB_NOT_FOUND", job_id=job_id)
        return 0

    if job_obj.state == "SUCCEEDED":
        _log_json("IDEMPOTENT_DONE", job_id=job_id, video_id=job_obj.video_id)
        return 0

    video = job_obj.video
    if video and video.status == "READY" and video.hls_path:
        job_complete(job_id, video.hls_path, video.duration)
        _log_json("IDEMPOTENT_READY", job_id=job_id, video_id=job_obj.video_id, reason="video_already_ready")
        return 0

    if not job_set_running(job_id):
        _log_json("JOB_ALREADY_TAKEN", job_id=job_id, state=job_obj.state)
        return 0

    cfg = load_config()
    progress = RedisProgressAdapter(ttl_seconds=VIDEO_PROGRESS_TTL_SECONDS)
    start_time = time.time()

    try:
        cache_video_status(job_obj.tenant_id, job_obj.video_id, "PROCESSING", ttl=21600)
    except Exception as e:
        logger.debug("cache PROCESSING failed: %s", e)

    job_dict = {
        "video_id": int(job_obj.video_id),
        "file_key": str(job_obj.video.file_key or ""),
        "tenant_id": int(job_obj.tenant_id),
        "tenant_code": "",
        "_job_id": job_id,
        "_cancel_check": lambda: job_is_cancel_requested(job_id) or _shutdown_event.is_set(),
        "_cancel_event": None,
    }
    try:
        tenant = job_obj.video.session.lecture.tenant
        job_dict["tenant_code"] = str(tenant.code)
    except Exception:
        pass

    _log_json("BATCH_PROCESS_START", job_id=job_id, video_id=job_obj.video_id, tenant_id=job_obj.tenant_id)

    heartbeat_thread = threading.Thread(target=_heartbeat_loop, args=(job_id,), daemon=True)
    heartbeat_thread.start()

    try:
        hls_path, duration = process_video(job=job_dict, cfg=cfg, progress=progress)
        ok, reason = job_complete(job_id, hls_path, duration)
        if not ok:
            raise RuntimeError(f"job_complete failed: {reason}")
        ...
        return 0
    except CancelledError:
        job_fail_retry(job_id, "CANCELLED")
        ...
        return 1
    except Exception as e:
        ...
        job_fail_retry(job_id, str(e)[:2000])
        ...
        return 1
    finally:
        _heartbeat_stop.set()


if __name__ == "__main__":
    sys.exit(main())
```

</details>

---

## 5) scan_stuck_video_jobs.py 전체 코드 원문

**파일 경로:** `apps/support/video/management/commands/scan_stuck_video_jobs.py`

<details>
<summary>전체 코드 (펼치기)</summary>

```python
# PATH: apps/support/video/management/commands/scan_stuck_video_jobs.py
"""
Stuck Scanner: RUNNING인데 last_heartbeat_at < now - 3분 → RETRY_WAIT, attempt_count++.

attempt_count >= MAX 이면 DEAD 처리.
RETRY_WAIT 전환 시 submit_batch_job 호출 (Batch 재제출).

Run via cron (e.g. every 2 min):
  python manage.py scan_stuck_video_jobs
"""
from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta

from apps.support.video.models import VideoTranscodeJob
from apps.support.video.services.batch_submit import submit_batch_job


STUCK_THRESHOLD_MINUTES = 3
MAX_ATTEMPTS = 5


class Command(BaseCommand):
    help = "Detect stuck RUNNING jobs (no heartbeat) → RETRY_WAIT or DEAD"

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Only log what would be done",
        )
        parser.add_argument(
            "--threshold",
            type=int,
            default=STUCK_THRESHOLD_MINUTES,
            help=f"Minutes without heartbeat to consider stuck (default: {STUCK_THRESHOLD_MINUTES})",
        )

    def handle(self, *args, **options):
        dry_run = options.get("dry_run", False)
        threshold_minutes = options.get("threshold", STUCK_THRESHOLD_MINUTES)
        cutoff = timezone.now() - timedelta(minutes=threshold_minutes)

        qs = VideoTranscodeJob.objects.filter(
            state=VideoTranscodeJob.State.RUNNING,
            last_heartbeat_at__lt=cutoff,
        ).order_by("id")

        recovered = 0
        dead = 0

        for job in qs:
            attempt_after = job.attempt_count + 1
            if attempt_after >= MAX_ATTEMPTS:
                if dry_run:
                    self.stdout.write(
                        f"DRY-RUN DEAD | job_id={job.id} video_id={job.video_id} attempt_count={job.attempt_count}"
                    )
                else:
                    job.state = VideoTranscodeJob.State.DEAD
                    job.error_code = "STUCK_MAX_ATTEMPTS"
                    job.error_message = f"Stuck (no heartbeat for {threshold_minutes}min)"
                    job.locked_by = ""
                    job.locked_until = None
                    job.save(update_fields=["state", "error_code", "error_message", "locked_by", "locked_until", "updated_at"])
                    self.stdout.write(self.style.WARNING(f"DEAD | job_id={job.id} video_id={job.video_id}"))
                dead += 1
            else:
                if dry_run:
                    self.stdout.write(
                        f"DRY-RUN RETRY_WAIT | job_id={job.id} video_id={job.video_id} attempt_count={job.attempt_count}→{attempt_after}"
                    )
                else:
                    job.state = VideoTranscodeJob.State.RETRY_WAIT
                    job.attempt_count = attempt_after
                    job.locked_by = ""
                    job.locked_until = None
                    job.save(update_fields=["state", "attempt_count", "locked_by", "locked_until", "updated_at"])
                    aws_job_id, submit_err = submit_batch_job(str(job.id))
                    if aws_job_id:
                        job.aws_batch_job_id = aws_job_id
                        job.save(update_fields=["aws_batch_job_id", "updated_at"])
                        self.stdout.write(
                            self.style.SUCCESS(f"RETRY_WAIT + BATCH_SUBMIT | job_id={job.id} video_id={job.video_id} attempt={attempt_after}")
                        )
                    else:
                        job.error_code = "BATCH_SUBMIT_FAILED"
                        job.error_message = (submit_err or "submit failed")[:2000]
                        job.save(update_fields=["error_code", "error_message", "updated_at"])
                        self.stderr.write(f"RETRY_WAIT (batch submit failed) | job_id={job.id} video_id={job.video_id}")
                recovered += 1

        self.stdout.write(
            self.style.SUCCESS(f"Done: recovered={recovered} dead={dead}" + (" (dry-run)" if dry_run else ""))
        )
```

</details>

---

## 6) Batch Job Definition 전체 JSON

**파일 경로:** `scripts/infra/batch/video_job_definition.json`

```json
{"jobDefinitionName":"academy-video-batch-jobdef","type":"container","containerProperties":{"image":"PLACEHOLDER_ECR_URI","vcpus":2,"memory":4096,"command":["python","-m","apps.worker.video_worker.batch_main","Ref::job_id"],"jobRoleArn":"PLACEHOLDER_JOB_ROLE_ARN","executionRoleArn":"PLACEHOLDER_EXECUTION_ROLE_ARN","resourceRequirements":[],"logConfiguration":{"logDriver":"awslogs","options":{"awslogs-group":"/aws/batch/academy-video-worker","awslogs-region":"PLACEHOLDER_REGION","awslogs-stream-prefix":"batch"}},"environment":[],"secrets":[],"mountPoints":[],"volumes":[],"linuxParameters":{}},"platformCapabilities":["EC2"],"parameters":{"job_id":""},"retryStrategy":{"attempts":1},"timeout":{"attemptDurationSeconds":14400}}
```

| 항목 | 값 |
|------|-----|
| **retryStrategy** | `{"attempts":1}` (Batch 자체 재시도 1회만) |
| **timeout** | `attemptDurationSeconds`: 14400 (4시간) |
| **command** | `["python","-m","apps.worker.video_worker.batch_main","Ref::job_id"]` |

---

## 7) 현재 Compute Environment 설정

- **코드/리포지터리 기준 JSON:** `scripts/infra/batch/video_compute_env.json`  
  - 이름: `academy-video-batch-ce`  
  - `computeResources.type`: **"EC2"** (Spot 아님)  
  - `allocationStrategy`: **"BEST_FIT_PROGRESSIVE"**  
  - `instanceTypes`: **["c6g.large","c6g.xlarge","c6g.2xlarge"]**  
  - `minvCpus`: **0**, `maxvCpus`: **32**  
  - **retryStrategy / timeout:** Job Definition에만 있음. CE JSON에는 없음.

- **실제 큐가 참조하는 CE:** `scripts/infra/batch/video_job_queue.json`  
  - `computeEnvironmentOrder`: `{"order":1,"computeEnvironment":"academy-video-batch-ce-v3"}`  
  - 즉 **현재 사용 중인 CE 이름은 `academy-video-batch-ce-v3`.**  
  - v3는 스크립트/다른 문서에서 ARM64, SLR, ECS AMI 등으로 설명되어 있으며, **동일 리포지터리에는 v3 전용 CE JSON 파일은 없고**, ps1에서 이름만 `academy-video-batch-ce-v3`로 참조함.

정리:

| 항목 | 소스 | 값 |
|------|------|-----|
| instanceTypes | video_compute_env.json | `["c6g.large","c6g.xlarge","c6g.2xlarge"]` |
| allocationStrategy | video_compute_env.json | `BEST_FIT_PROGRESSIVE` |
| computeResources.type | video_compute_env.json | `EC2` |
| minvCpus / maxvCpus | video_compute_env.json | `0` / `32` |
| retryStrategy | Job Definition | `{"attempts":1}` (CE 아님) |
| timeout | Job Definition | `attemptDurationSeconds`: 14400 |
| 현재 큐가 바라보는 CE | video_job_queue.json | `academy-video-batch-ce-v3` |

---

## 8) Video 모델 정의

**파일 경로:** `apps/support/video/models.py` (L33–146)

- **current_job FK:**

```python
# L128–135
current_job = models.ForeignKey(
    "VideoTranscodeJob",
    on_delete=models.SET_NULL,
    null=True,
    blank=True,
    related_name="+",
    help_text="현재 transcoding Job (진행 중 또는 최종)",
)
```

- **tenant_id 필드 존재 여부:**  
  **Video 모델에는 `tenant_id` 필드 없음.**  
  tenant는 `video.session.lecture.tenant` (및 `session__lecture__tenant_id`) 로만 접근.

- **HLS 경로 저장 방식:**  
  - 필드: `hls_path = models.CharField(max_length=500, blank=True, help_text="HLS master playlist path (relative to CDN root)")` (L109–114)  
  - 값 생성: `apps/core/r2_paths.py`의 `video_hls_master_path(tenant_id, video_id)` → `f"tenants/{tenant_id}/video/hls/{video_id}/master.m3u8"`  
  - 워커가 완료 시 `job_complete(job_id, hls_path, duration)` 등으로 DB에 반영.

---

## 9) 멀티테넌트 구조

| 질문 | 답 (코드/문서 기준) |
|------|----------------------|
| **현재 1 DB인가?** | 예. 단일 DB 사용. 테넌트 구분은 `tenant_id`/`session__lecture__tenant_id` 등으로 행 단위 스코프. |
| **tenant_id는 모든 Video/Job에 필수인가?** | **Video:** 필드 없음. Session→Lecture→Tenant 관계로 tenant 파생. **VideoTranscodeJob:** `tenant_id` 필드 있음, NOT null → Job에는 필수. |
| **DB 분리 계획은 언제 단계인가?** | 코드/문서에서 “DB 분리”, “schema per tenant”, “database per tenant” 같은 **구체적 단계/로드맵은 없음.** `r2_paths.py` 등에 “멀티테넌트 + Aurora 확장 대비” 문구만 있음. |

---

## 요약 표

| # | 항목 | 파일 경로 | 요약 |
|---|------|-----------|------|
| 1 | VideoTranscodeJob | `apps/support/video/models.py` | State 7종, aws_batch_job_id/tenant_id 있음, created_at/updated_at auto |
| 2 | submit_batch_job | `apps/support/video/services/batch_submit.py` | 반환 (aws_job_id, None) or (None, err). DB 저장은 video_encoding.py L49–50. retryStrategy는 Job Def에만 |
| 3 | process_video | `src/infrastructure/video/processor.py` | ffmpeg는 transcoder.transcode_to_hls의 Popen. blocking, 단계별 루프+취소 체크 가능 |
| 4 | batch_main | `apps/worker/video_worker/batch_main.py` | 위 원문 참고 |
| 5 | scan_stuck_video_jobs | `apps/support/video/management/commands/scan_stuck_video_jobs.py` | 위 원문 참고 |
| 6 | Job Definition | `scripts/infra/batch/video_job_definition.json` | retryStrategy.attempts=1, timeout 14400 |
| 7 | Compute Environment | `scripts/infra/batch/video_compute_env.json` + queue는 v3 참조 | type=EC2, BEST_FIT_PROGRESSIVE, c6g.*, min 0 max 32. 실제 큐는 ce-v3 |
| 8 | Video | `apps/support/video/models.py` | current_job FK(SET_NULL), tenant_id 없음, hls_path CharField, 경로는 r2_paths.video_hls_master_path |
| 9 | 멀티테넌트 | — | 단일 DB. Job엔 tenant_id 필수, Video엔 없음. DB 분리 계획 단계는 문서/코드에 없음 |
