"""
AWS Batch Job 제어 — Terminate 등.

Video 삭제 시 진행 중인 Batch Job을 즉시 중단하기 위해 사용.
호출부는 best-effort로 사용하며, 실패해도 삭제 요청은 성공으로 처리.
"""

from __future__ import annotations

import logging
import random
import time
from typing import Optional

logger = logging.getLogger(__name__)

# Terminal Batch job statuses: no need to call TerminateJob
_BATCH_TERMINAL = frozenset({"SUCCEEDED", "FAILED", "SUBMITTED", "PENDING", "RUNNABLE"})
# Actually terminal (job finished or will not run container again)
_BATCH_JOB_TERMINAL = frozenset({"SUCCEEDED", "FAILED"})


def terminate_batch_job(
    aws_batch_job_id: str,
    reason: str,
    *,
    video_id: Optional[int] = None,
    job_id: Optional[str] = None,
    _describe_first: bool = True,
) -> None:
    """
    AWS Batch Job을 즉시 종료. Best-effort; 예외를 발생시키지 않음.

    - Optionally describes job first; if already SUCCEEDED/FAILED, skips terminate (idempotent).
    - Retries up to 3 times with exponential backoff + jitter on transient errors.
    - Logs AccessDenied vs transient separately (VIDEO_DELETE_TERMINATE_FAILED payload.error_type).
    """
    aws_batch_job_id = (aws_batch_job_id or "").strip()
    if not aws_batch_job_id:
        return

    from apps.support.video.services.ops_events import emit_ops_event

    emit_ops_event(
        "VIDEO_DELETE_TERMINATE_REQUESTED",
        severity="INFO",
        video_id=video_id,
        job_id=job_id,
        aws_batch_job_id=aws_batch_job_id,
        payload={"reason": (reason or "video_deleted")[:256]},
    )

    region = _batch_region()

    if _describe_first:
        try:
            import boto3
            client = boto3.client("batch", region_name=region)
            desc = client.describe_jobs(jobs=[aws_batch_job_id])
            jobs = desc.get("jobs") or []
            if jobs:
                status = (jobs[0].get("status") or "").strip().upper()
                if status in _BATCH_JOB_TERMINAL:
                    logger.info(
                        "VIDEO_DELETE_TERMINATE_SKIP_ALREADY_TERMINAL | video_id=%s job_id=%s aws_batch_job_id=%s status=%s",
                        video_id, job_id, aws_batch_job_id, status,
                    )
                    return
        except Exception as e:
            # DescribeJobs optional; on failure proceed to terminate (e.g. no permission)
            if "AccessDenied" in str(e) or "UnauthorizedException" in str(e):
                logger.debug(
                    "VIDEO_DELETE_TERMINATE describe skipped (no permission): %s", e,
                )
            else:
                logger.debug("VIDEO_DELETE_TERMINATE describe failed (will retry terminate): %s", e)

    reason_str = (reason or "video_deleted")[:256]
    max_attempts = 3
    base_delay = 0.5
    last_error = None
    is_access_denied = False

    for attempt in range(max_attempts):
        try:
            import boto3
            client = boto3.client("batch", region_name=region)
            client.terminate_job(jobId=aws_batch_job_id, reason=reason_str)
            logger.info(
                "VIDEO_DELETE_TERMINATE_OK | video_id=%s job_id=%s aws_batch_job_id=%s",
                video_id, job_id, aws_batch_job_id,
            )
            return
        except Exception as e:
            last_error = e
            err_str = str(e)
            is_access_denied = "AccessDenied" in err_str or "UnauthorizedException" in err_str
            if is_access_denied:
                break
            if attempt < max_attempts - 1:
                delay = base_delay * (2 ** attempt) + random.uniform(0, 0.3)
                time.sleep(delay)

    logger.warning(
        "VIDEO_DELETE_TERMINATE_FAILED | video_id=%s job_id=%s aws_batch_job_id=%s error=%s",
        video_id, job_id, aws_batch_job_id, last_error,
    )
    emit_ops_event(
        "VIDEO_DELETE_TERMINATE_FAILED",
        severity="WARNING",
        video_id=video_id,
        job_id=job_id,
        aws_batch_job_id=aws_batch_job_id,
        payload={
            "error": str(last_error)[:500],
            "error_type": "AccessDenied" if is_access_denied else "transient",
        },
    )


def _batch_region() -> str:
    """Batch 클라이언트용 리전 (기존 batch_submit / settings 방식)."""
    from django.conf import settings
    return (
        getattr(settings, "AWS_REGION", None)
        or getattr(settings, "AWS_DEFAULT_REGION", None)
        or "ap-northeast-2"
    )
