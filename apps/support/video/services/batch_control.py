"""
AWS Batch Job 제어 — Terminate 등.

Video 삭제 시 진행 중인 Batch Job을 즉시 중단하기 위해 사용.
호출부는 best-effort로 사용하며, 실패해도 삭제 요청은 성공으로 처리.
"""

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)


def terminate_batch_job(
    aws_batch_job_id: str,
    reason: str,
    *,
    video_id: Optional[int] = None,
    job_id: Optional[str] = None,
) -> None:
    """
    AWS Batch Job을 즉시 종료. Best-effort; 예외를 발생시키지 않음.

    Args:
        aws_batch_job_id: Batch job id (예: abc12345-...)
        reason: terminate_job reason (최대 256자)
        video_id: 로깅용 video_id
        job_id: 로깅용 VideoTranscodeJob id (UUID 문자열)

    삭제 실패(권한/네트워크/없는 job)해도 호출부는 성공으로 간주.
    VIDEO_DELETE_TERMINATE_REQUESTED / VIDEO_DELETE_TERMINATE_FAILED 로그로 운영 확인.
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
    try:
        import boto3
        client = boto3.client("batch", region_name=region)
        client.terminate_job(jobId=aws_batch_job_id, reason=(reason or "video_deleted")[:256])
        logger.info(
            "VIDEO_DELETE_TERMINATE_OK | video_id=%s job_id=%s aws_batch_job_id=%s",
            video_id, job_id, aws_batch_job_id,
        )
    except Exception as e:
        logger.warning(
            "VIDEO_DELETE_TERMINATE_FAILED | video_id=%s job_id=%s aws_batch_job_id=%s error=%s",
            video_id, job_id, aws_batch_job_id, e,
        )
        emit_ops_event(
            "VIDEO_DELETE_TERMINATE_FAILED",
            severity="WARNING",
            video_id=video_id,
            job_id=job_id,
            aws_batch_job_id=aws_batch_job_id,
            payload={"error": str(e)[:500]},
        )


def _batch_region() -> str:
    """Batch 클라이언트용 리전 (기존 batch_submit / settings 방식)."""
    from django.conf import settings
    return (
        getattr(settings, "AWS_REGION", None)
        or getattr(settings, "AWS_DEFAULT_REGION", None)
        or "ap-northeast-2"
    )
