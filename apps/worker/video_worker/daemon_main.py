"""
Video Worker - Daemon Mode (상주 프로세스)

DB 폴링 기반 장기 실행 데몬. Batch 컨테이너 대신 상주하며 QUEUED 작업을 처리.
- 30분(1800초) 이하 영상만 처리
- 시작 시 + 주기적 연결 검증 (DB, Redis)
- Graceful shutdown (SIGTERM/SIGINT → 현재 작업 완료 후 종료)
- Idle backoff (작업 없으면 폴링 간격 점진 증가)
"""

from __future__ import annotations

import json
import logging
import os
import signal
import sys
import threading
import time
from typing import Optional

# Must be set by entrypoint from SSM; no fallback.
if not os.environ.get("DJANGO_SETTINGS_MODULE"):
    print("daemon_main: DJANGO_SETTINGS_MODULE not set. Run via batch_entrypoint.", file=sys.stderr)
    sys.exit(1)

import django

django.setup()

from django.db import connection
from django.utils import timezone

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

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
logger = logging.getLogger("video_worker_daemon")

# ── Settings ─────────────────────────────────────────────────
VIDEO_PROGRESS_TTL_SECONDS = int(os.getenv("VIDEO_PROGRESS_TTL_SECONDS", "86400"))
VIDEO_JOB_MAX_ATTEMPTS = int(os.getenv("VIDEO_JOB_MAX_ATTEMPTS", "5"))
VIDEO_JOB_HEARTBEAT_SECONDS = int(os.getenv("VIDEO_JOB_HEARTBEAT_SECONDS", "60"))

DAEMON_POLL_INTERVAL = int(os.getenv("DAEMON_POLL_INTERVAL_SECONDS", "5"))
DAEMON_POLL_MAX_INTERVAL = int(os.getenv("DAEMON_POLL_MAX_INTERVAL_SECONDS", "30"))
DAEMON_MAX_DURATION_SECONDS = int(os.getenv("DAEMON_MAX_DURATION_SECONDS", "1800"))  # 30분
DAEMON_HEALTH_CHECK_INTERVAL = int(os.getenv("DAEMON_HEALTH_CHECK_INTERVAL_SECONDS", "300"))  # 5분

# ── Shared state ─────────────────────────────────────────────
_shutdown_event = threading.Event()
_heartbeat_stop = threading.Event()
_current_job_id: list[Optional[str]] = [None]


# ── Signal handlers ──────────────────────────────────────────
def _handle_term(signum: int, frame: object) -> None:
    """SIGTERM/SIGINT → graceful shutdown. 현재 작업은 완료까지 기다림."""
    logger.info(json.dumps({"event": "DAEMON_SHUTDOWN_SIGNAL", "signal": signum}))
    _shutdown_event.set()


# ── Logging ──────────────────────────────────────────────────
def _log(event: str, **kwargs) -> None:
    payload = {"event": event, "ts": time.time()}
    payload.update(kwargs)
    logger.info(json.dumps(payload))


# ── Connection verification ──────────────────────────────────
def verify_db_connection() -> bool:
    """DB 연결 확인."""
    try:
        connection.ensure_connection()
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
        return True
    except Exception as e:
        _log("DB_CONNECTION_FAILED", error=str(e))
        return False


def verify_redis_connection() -> bool:
    """Redis 연결 확인."""
    try:
        from django_redis import get_redis_connection
        redis = get_redis_connection("default")
        redis.ping()
        return True
    except Exception as e:
        _log("REDIS_CONNECTION_FAILED", error=str(e))
        return False


def verify_connections() -> bool:
    """DB + Redis 연결 검증. 둘 다 성공해야 True."""
    db_ok = verify_db_connection()
    redis_ok = verify_redis_connection()
    if db_ok and redis_ok:
        _log("CONNECTION_VERIFY_OK")
        return True
    _log("CONNECTION_VERIFY_FAILED", db=db_ok, redis=redis_ok)
    return False


# ── Job polling ──────────────────────────────────────────────
def poll_next_job():
    """
    QUEUED 또는 RETRY_WAIT 상태의 작업 중 duration <= 30분인 것을 하나 가져옴.
    duration이 NULL인 경우도 포함 (ffprobe 실패 시 워커에서 재검증).
    """
    from apps.support.video.models import VideoTranscodeJob
    from django.db.models import Q

    return (
        VideoTranscodeJob.objects
        .select_related("video", "video__session", "video__session__lecture", "video__session__lecture__tenant")
        .filter(
            state__in=[VideoTranscodeJob.State.QUEUED, VideoTranscodeJob.State.RETRY_WAIT],
        )
        .filter(
            Q(video__duration__isnull=True) | Q(video__duration__lte=DAEMON_MAX_DURATION_SECONDS)
        )
        .order_by("created_at")
        .first()
    )


# ── Heartbeat ────────────────────────────────────────────────
def _heartbeat_loop(job_id: str, video_id: int) -> None:
    from apps.support.video.services.video_job_lock import extend as lock_extend
    from django.conf import settings
    lock_ttl = int(getattr(settings, "VIDEO_JOB_LOCK_TTL_SECONDS", 43200))

    while not _heartbeat_stop.is_set():
        if _heartbeat_stop.wait(timeout=VIDEO_JOB_HEARTBEAT_SECONDS):
            break
        if _shutdown_event.is_set():
            break
        try:
            job_heartbeat(job_id, lease_seconds=VIDEO_JOB_HEARTBEAT_SECONDS * 2)
            lock_extend(video_id, ttl_seconds=lock_ttl)
        except Exception as e:
            logger.debug("heartbeat failed: %s", e)


def _video_still_exists(video_id: int) -> bool:
    from apps.support.video.models import Video
    return Video.objects.filter(pk=video_id).exists()


# ── Process one job ──────────────────────────────────────────
def process_one_job(job_obj) -> int:
    """
    단일 작업 처리. batch_main.main()과 동일한 로직.
    Returns: 0=success, 1=failure
    """
    job_id = str(job_obj.id)
    _current_job_id[0] = job_id
    _heartbeat_stop.clear()

    tid = getattr(job_obj, "tenant_id", None)
    vid = getattr(job_obj, "video_id", None)

    # Idempotency checks
    if job_obj.state == "SUCCEEDED":
        _log("IDEMPOTENT_DONE", job_id=job_id, tenant_id=tid, video_id=vid)
        return 0

    video = job_obj.video
    if video and video.status == "READY" and video.hls_path:
        job_complete(job_id, video.hls_path, video.duration)
        _log("IDEMPOTENT_READY", job_id=job_id, tenant_id=tid, video_id=vid)
        return 0

    if not job_set_running(job_id):
        _log("JOB_ALREADY_TAKEN", job_id=job_id, tenant_id=tid, video_id=vid, state=job_obj.state)
        return 0

    if not _video_still_exists(vid):
        _log("VIDEO_DELETED", job_id=job_id, tenant_id=tid, video_id=vid)
        return 0

    cfg = load_config()
    progress = RedisProgressAdapter(ttl_seconds=VIDEO_PROGRESS_TTL_SECONDS)
    start_time = time.time()

    try:
        cache_video_status(tid, vid, "PROCESSING", ttl=21600)
    except Exception:
        pass

    job_dict = {
        "video_id": int(vid),
        "file_key": str(video.file_key or ""),
        "tenant_id": int(tid),
        "tenant_code": "",
        "_job_id": job_id,
        "_cancel_check": lambda: (
            job_is_cancel_requested(job_id)
            or _shutdown_event.is_set()
            or not _video_still_exists(vid)
        ),
        "_cancel_event": None,
    }
    try:
        tenant = video.session.lecture.tenant
        job_dict["tenant_code"] = str(tenant.code)
    except Exception:
        pass

    _log("DAEMON_PROCESS_START", job_id=job_id, tenant_id=tid, video_id=vid,
         duration=getattr(video, "duration", None))

    heartbeat_thread = threading.Thread(
        target=_heartbeat_loop, args=(job_id, int(vid)), daemon=True
    )
    heartbeat_thread.start()

    try:
        hls_path, duration = process_video(job=job_dict, cfg=cfg, progress=progress)

        if not _video_still_exists(vid):
            _log("VIDEO_DELETED_BEFORE_COMPLETE", job_id=job_id, tenant_id=tid, video_id=vid)
            return 0

        ok, reason = job_complete(job_id, hls_path, duration)
        if not ok:
            raise RuntimeError(f"job_complete failed: {reason}")

        elapsed = time.time() - start_time
        _log("DAEMON_JOB_COMPLETED", job_id=job_id, tenant_id=tid, video_id=vid,
             elapsed_sec=round(elapsed, 2))

        # R2 raw 파일 삭제
        file_key = job_dict.get("file_key", "").strip()
        if file_key:
            for attempt in range(3):
                try:
                    from apps.infrastructure.storage.r2 import delete_object_r2_video
                    delete_object_r2_video(key=file_key)
                    _log("R2_RAW_DELETED", job_id=job_id, video_id=vid, key_prefix=file_key[:80])
                    break
                except Exception as e:
                    logger.warning("R2 raw delete failed video_id=%s attempt=%s: %s", vid, attempt + 1, e)
                    if attempt < 2:
                        time.sleep(2 ** attempt)

        return 0

    except CancelledError:
        if not _video_still_exists(vid):
            _log("VIDEO_DELETED_CANCELLED", job_id=job_id, tenant_id=tid, video_id=vid)
            return 0
        job_fail_retry(job_id, "CANCELLED")
        _log("DAEMON_JOB_CANCELLED", job_id=job_id, tenant_id=tid, video_id=vid)
        return 1

    except Exception as e:
        logger.exception("DAEMON_JOB_FAILED | job_id=%s | error=%s", job_id, e)
        job_fail_retry(job_id, str(e)[:2000])
        job_after = job_get_by_id(job_id)
        if job_after and job_after.attempt_count >= VIDEO_JOB_MAX_ATTEMPTS:
            job_mark_dead(job_id, error_code="MAX_ATTEMPTS", error_message=str(e)[:2000])
            _log("DAEMON_JOB_DEAD", job_id=job_id, tenant_id=tid, video_id=vid,
                 attempt_count=job_after.attempt_count)
        return 1
    finally:
        _heartbeat_stop.set()
        _current_job_id[0] = None


# ── Main daemon loop ─────────────────────────────────────────
def main() -> int:
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _handle_term)
    if hasattr(signal, "SIGINT"):
        signal.signal(signal.SIGINT, _handle_term)

    _log("DAEMON_STARTING",
         max_duration=DAEMON_MAX_DURATION_SECONDS,
         poll_interval=DAEMON_POLL_INTERVAL,
         poll_max_interval=DAEMON_POLL_MAX_INTERVAL)

    # ── 시작 시 연결 검증 (필수) ──
    for attempt in range(5):
        if verify_connections():
            break
        if attempt == 4:
            _log("DAEMON_STARTUP_FAILED", reason="connection_verify_failed_after_5_attempts")
            return 1
        _log("DAEMON_STARTUP_RETRY", attempt=attempt + 1)
        time.sleep(5)

    _log("DAEMON_STARTED")

    current_interval = DAEMON_POLL_INTERVAL
    last_health_check = time.time()
    jobs_processed = 0

    while not _shutdown_event.is_set():
        # ── 주기적 연결 검증 ──
        now = time.time()
        if now - last_health_check >= DAEMON_HEALTH_CHECK_INTERVAL:
            if not verify_connections():
                _log("DAEMON_HEALTH_FAILED", action="wait_and_retry")
                # 연결 실패 시 잠시 대기 후 재시도
                if _shutdown_event.wait(timeout=10):
                    break
                # DB 연결 리셋
                try:
                    connection.close()
                except Exception:
                    pass
                continue
            last_health_check = now

        # ── 작업 폴링 ──
        try:
            job_obj = poll_next_job()
        except Exception as e:
            _log("DAEMON_POLL_ERROR", error=str(e))
            # DB 연결이 끊겼을 수 있으므로 리셋
            try:
                connection.close()
            except Exception:
                pass
            if _shutdown_event.wait(timeout=current_interval):
                break
            continue

        if job_obj is None:
            # 작업 없음 → backoff
            if _shutdown_event.wait(timeout=current_interval):
                break
            current_interval = min(current_interval + DAEMON_POLL_INTERVAL, DAEMON_POLL_MAX_INTERVAL)
            continue

        # 작업 발견 → 즉시 처리, 간격 리셋
        current_interval = DAEMON_POLL_INTERVAL
        result = process_one_job(job_obj)
        jobs_processed += 1

        if result == 0:
            _log("DAEMON_JOB_OK", jobs_processed=jobs_processed)
        else:
            _log("DAEMON_JOB_FAIL", jobs_processed=jobs_processed)

        # shutdown 체크 (작업 완료 후)
        if _shutdown_event.is_set():
            break

    _log("DAEMON_STOPPED", jobs_processed=jobs_processed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
