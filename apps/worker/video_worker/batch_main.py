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


# Must be set by batch_entrypoint from SSM; no fallback.
if not os.environ.get("DJANGO_SETTINGS_MODULE"):
    print("batch_main: DJANGO_SETTINGS_MODULE not set. Run via batch_entrypoint (SSM JSON).", file=sys.stderr)
    sys.exit(1)

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

VIDEO_PROGRESS_TTL_SECONDS = int(os.getenv("VIDEO_PROGRESS_TTL_SECONDS", "86400"))
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


def _heartbeat_loop(job_id: str, video_id: int) -> None:
    """RUNNING job의 last_heartbeat_at 갱신 + DDB lock lease 연장 (1 video 1 job 보장)."""
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


def _log_json(event: str, job_id: str, tenant_id: int = None, video_id: int = None, aws_batch_job_id: str = "", **kwargs) -> None:
    """Structured log: every batch_main log includes job_id, tenant_id, video_id, aws_batch_job_id."""
    payload = {"event": event, "job_id": job_id, "tenant_id": tenant_id, "video_id": video_id, "aws_batch_job_id": aws_batch_job_id or ""}
    payload.update(kwargs)
    logger.info(json.dumps(payload))


def _is_valid_uuid(s: str) -> bool:
    if not s or len(s) != 36:
        return False
    try:
        import uuid
        uuid.UUID(s)
        return True
    except (ValueError, TypeError):
        return False


def _video_still_exists(video_id: int) -> bool:
    """Video 행이 아직 존재하는지 (삭제/취소 시 Worker 중단 판단용)."""
    from apps.support.video.models import Video
    return Video.objects.filter(pk=video_id).exists()


def main() -> int:
    job_id = os.environ.get("VIDEO_JOB_ID") or (sys.argv[1] if len(sys.argv) > 1 else None)
    if not job_id:
        _log_json("BATCH_MAIN_ERROR", job_id=job_id or "", error="VIDEO_JOB_ID or argv[1] required")
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
    aws_batch_job_id = (getattr(job_obj, "aws_batch_job_id", None) or "")
    tid = getattr(job_obj, "tenant_id", None)
    vid = getattr(job_obj, "video_id", None)

    if job_obj.state == "SUCCEEDED":
        _log_json("IDEMPOTENT_DONE", job_id=job_id, tenant_id=job_obj.tenant_id, video_id=job_obj.video_id, aws_batch_job_id=aws_batch_job_id)
        return 0

    video = job_obj.video
    if video and video.status == "READY" and video.hls_path:
        job_complete(job_id, video.hls_path, video.duration)
        _log_json("IDEMPOTENT_READY", job_id=job_id, tenant_id=job_obj.tenant_id, video_id=job_obj.video_id, aws_batch_job_id=aws_batch_job_id, reason="video_already_ready")
        return 0

    if not job_set_running(job_id):
        _log_json("JOB_ALREADY_TAKEN", job_id=job_id, tenant_id=job_obj.tenant_id, video_id=job_obj.video_id, aws_batch_job_id=aws_batch_job_id, state=job_obj.state)
        return 0

    if not _video_still_exists(job_obj.video_id):
        _log_json("WORKER_CANCELLED_BY_VIDEO_DELETE", job_id=job_id, tenant_id=tid, video_id=job_obj.video_id, aws_batch_job_id=aws_batch_job_id, reason="video_deleted_or_cancelled")
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
        "_cancel_check": lambda: job_is_cancel_requested(job_id) or _shutdown_event.is_set() or not _video_still_exists(job_obj.video_id),
        "_cancel_event": None,
    }
    try:
        tenant = job_obj.video.session.lecture.tenant
        job_dict["tenant_code"] = str(tenant.code)
    except Exception:
        pass

    _log_json(
        "BATCH_PROCESS_START",
        job_id=job_id,
        tenant_id=job_obj.tenant_id,
        video_id=job_obj.video_id,
        aws_batch_job_id=aws_batch_job_id,
    )

    heartbeat_thread = threading.Thread(target=_heartbeat_loop, args=(job_id, int(job_obj.video_id)), daemon=True)
    heartbeat_thread.start()

    try:
        hls_path, duration = process_video(job=job_dict, cfg=cfg, progress=progress)
        if not _video_still_exists(job_obj.video_id):
            _log_json("WORKER_CANCELLED_BY_VIDEO_DELETE", job_id=job_id, tenant_id=job_obj.tenant_id, video_id=job_obj.video_id, aws_batch_job_id=aws_batch_job_id, reason="video_deleted_before_complete")
            return 0
        ok, reason = job_complete(job_id, hls_path, duration)
        if not ok:
            raise RuntimeError(f"job_complete failed: {reason}")

        elapsed = time.time() - start_time
        _log_json(
            "BATCH_JOB_COMPLETED",
            job_id=job_id,
            tenant_id=job_obj.tenant_id,
            video_id=job_obj.video_id,
            aws_batch_job_id=aws_batch_job_id,
            duration_sec=round(elapsed, 2),
        )

        file_key = job_dict.get("file_key", "").strip()
        if file_key:
            for attempt in range(3):
                try:
                    from apps.infrastructure.storage.r2 import delete_object_r2_video

                    delete_object_r2_video(key=file_key)
                    _log_json("R2_RAW_DELETED", job_id=job_id, tenant_id=job_obj.tenant_id, video_id=job_obj.video_id, aws_batch_job_id=aws_batch_job_id, key_prefix=file_key[:80])
                    break
                except Exception as e:
                    logger.warning("R2 raw delete failed video_id=%s attempt=%s: %s", job_obj.video_id, attempt + 1, e)
                    if attempt < 2:
                        time.sleep(2**attempt)

        return 0

    except CancelledError:
        if not _video_still_exists(job_obj.video_id):
            _log_json("WORKER_CANCELLED_BY_VIDEO_DELETE", job_id=job_id, tenant_id=job_obj.tenant_id, video_id=job_obj.video_id, aws_batch_job_id=aws_batch_job_id, reason="video_deleted_or_cancelled")
            return 0
        job_fail_retry(job_id, "CANCELLED")
        _log_json("BATCH_JOB_CANCELLED", job_id=job_id, tenant_id=job_obj.tenant_id, video_id=job_obj.video_id, aws_batch_job_id=aws_batch_job_id)
        return 1

    except Exception as e:
        logger.exception("BATCH_JOB_FAILED | job_id=%s | error=%s", job_id, e)
        job_fail_retry(job_id, str(e)[:2000])
        job_after = job_get_by_id(job_id)
        if job_after and job_after.attempt_count >= VIDEO_JOB_MAX_ATTEMPTS:
            job_mark_dead(job_id, error_code="MAX_ATTEMPTS", error_message=str(e)[:2000])
            _log_json("BATCH_JOB_DEAD", job_id=job_id, tenant_id=job_obj.tenant_id, video_id=job_obj.video_id, aws_batch_job_id=aws_batch_job_id, attempt_count=job_after.attempt_count)
        return 1
    finally:
        _heartbeat_stop.set()


if __name__ == "__main__":
    sys.exit(main())
