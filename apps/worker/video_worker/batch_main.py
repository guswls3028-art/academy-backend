"""
Video Worker - AWS Batch 엔트리포인트

Single-execution contract. NO job_set_running. NO RUNNING state block.
State transitions ONLY via job_complete().
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "apps.api.config.settings.worker")
import django

django.setup()

from academy.adapters.db.django.repositories_video import (
    job_get_by_id,
    job_complete,
    job_fail_retry,
    job_mark_dead,
    job_is_cancel_requested,
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
        "_cancel_check": lambda: job_is_cancel_requested(job_id),
        "_cancel_event": None,
    }
    try:
        tenant = job_obj.video.session.lecture.tenant
        job_dict["tenant_code"] = str(tenant.code)
    except Exception:
        pass

    _log_json("BATCH_PROCESS_START", job_id=job_id, video_id=job_obj.video_id, tenant_id=job_obj.tenant_id)

    try:
        hls_path, duration = process_video(job=job_dict, cfg=cfg, progress=progress)
        ok, reason = job_complete(job_id, hls_path, duration)
        if not ok:
            raise RuntimeError(f"job_complete failed: {reason}")

        elapsed = time.time() - start_time
        _log_json(
            "BATCH_JOB_COMPLETED",
            job_id=job_id,
            video_id=job_obj.video_id,
            tenant_id=job_obj.tenant_id,
            duration_sec=round(elapsed, 2),
        )

        file_key = job_dict.get("file_key", "").strip()
        if file_key:
            for attempt in range(3):
                try:
                    from apps.infrastructure.storage.r2 import delete_object_r2_video

                    delete_object_r2_video(key=file_key)
                    _log_json("R2_RAW_DELETED", video_id=job_obj.video_id, key_prefix=file_key[:80])
                    break
                except Exception as e:
                    logger.warning("R2 raw delete failed video_id=%s attempt=%s: %s", job_obj.video_id, attempt + 1, e)
                    if attempt < 2:
                        time.sleep(2**attempt)

        return 0

    except CancelledError:
        job_fail_retry(job_id, "CANCELLED")
        _log_json("BATCH_JOB_CANCELLED", job_id=job_id, video_id=job_obj.video_id)
        return 1

    except Exception as e:
        logger.exception("BATCH_JOB_FAILED | job_id=%s | error=%s", job_id, e)
        job_fail_retry(job_id, str(e)[:2000])
        job_after = job_get_by_id(job_id)
        if job_after and job_after.attempt_count >= VIDEO_JOB_MAX_ATTEMPTS:
            job_mark_dead(job_id, error_code="MAX_ATTEMPTS", error_message=str(e)[:2000])
            _log_json("BATCH_JOB_DEAD", job_id=job_id, video_id=job_obj.video_id, attempt_count=job_after.attempt_count)
        return 1


if __name__ == "__main__":
    sys.exit(main())
