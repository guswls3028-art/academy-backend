"""
ProcessVideoJobHandler - Video 작업 처리 유스케이스

흐름:
1. Idempotency 락 획득 (Redis SETNX) - 반드시 먼저
2. Repository.mark_processing (DB)
3. Processor 실행 (progress는 Redis에만 기록, Write-Behind)
4. Repository.complete_video (DB) 또는 fail_video (실패 시)
5. Idempotency 락 해제
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Optional

from src.application.ports.idempotency import IIdempotency
from src.application.ports.progress import IProgress
from src.application.ports.video_repository import IVideoRepository

logger = logging.getLogger(__name__)


# Processor 시그니처: (job, cfg, progress) -> (hls_path, duration)
ProcessVideoFn = Callable[[dict, Any, IProgress], tuple[str, int]]


class ProcessVideoJobHandler:
    """
    Video 작업 처리 Handler

    멱등성 락 -> DB 상태 변경 -> 처리 -> DB 완료/실패 -> 락 해제
    """

    def __init__(
        self,
        repo: IVideoRepository,
        idempotency: IIdempotency,
        progress: IProgress,
        process_fn: ProcessVideoFn,
    ) -> None:
        self._repo = repo
        self._idempotency = idempotency
        self._progress = progress
        self._process_fn = process_fn

    def handle(self, job: dict, cfg: Any) -> str:
        """
        작업 처리

        Returns:
            "ok" | "skip" | "failed"
        """
        video_id = int(job.get("video_id", 0))
        job_id = f"encode:{video_id}"  # action별 멱등 키 분리 (delete_r2는 delete_r2:{video_id})

        if not self._idempotency.acquire_lock(job_id):
            return "skip"

        try:
            if not self._repo.mark_processing(video_id):
                logger.warning("Cannot mark video %s as PROCESSING, skipping", video_id)
                return "skip"

            hls_path, duration = self._process_fn(job, cfg, self._progress)

            ok, reason = self._repo.complete_video(
                video_id=video_id,
                hls_path=hls_path,
                duration=duration,
            )
            if not ok:
                raise RuntimeError(f"Failed to complete video: {reason}")

            logger.info("Video processing completed: video_id=%s, duration=%s", video_id, duration)
            return "ok"

        except Exception as e:
            logger.exception("Video processing failed: video_id=%s, error=%s", video_id, e)
            self._repo.fail_video(video_id=video_id, reason=str(e)[:2000])
            return "failed"

        finally:
            self._idempotency.release_lock(job_id)


# Typo fix: idempotency -> self._idempotency
# Let me fix the handler - I used idempotency instead of self._idempotency