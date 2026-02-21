"""
ProcessVideoJobHandler - Video 작업 처리 유스케이스

흐름:
1. (재시도 시) 취소 요청 확인 → 있으면 스킵
2. Idempotency 락 획득 (Redis SETNX) - 반드시 먼저
3. Repository.mark_processing (DB)
4. Processor 실행 (progress는 Redis에만 기록, Write-Behind)
5. Repository.complete_video (DB) 또는 fail_video (실패 시)
6. Idempotency 락 해제
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Optional

from src.application.ports.idempotency import IIdempotency
from src.application.ports.progress import IProgress
from src.application.ports.video_repository import IVideoRepository

logger = logging.getLogger(__name__)


class CancelledError(RuntimeError):
    """재시도로 인한 취소 요청 시 processor에서 발생 (DB fail_video 호출 없이 스킵)."""
    pass


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
            "ok" | "skip:cancel" | "skip:mark_processing" | "skip:claim" | "lock_fail" | "failed"

            - "ok": 처리 성공
            - "skip:cancel": 취소 요청 또는 처리 중 취소 → ACK(delete)
            - "skip:mark_processing": mark_processing 실패 (legacy 모드)
            - "skip:claim": try_claim 실패 (fast ACK 모드, 메시지 이미 delete됨)
            - "lock_fail": Redis 락 획득 실패 (legacy 모드)
            - "failed": 처리 실패
        """
        video_id = int(job.get("video_id", 0))
        tenant_id = int(job.get("tenant_id", 0)) if job.get("tenant_id") is not None else None
        job_id = f"encode:{video_id}"
        worker_id = job.get("_worker_id")
        use_fast_ack = bool(worker_id)

        # 재시도 시 API가 설정한 취소 요청이 있으면 이 메시지는 스킵
        if tenant_id is not None:
            try:
                from apps.support.video.redis_status_cache import is_cancel_requested
                if is_cancel_requested(tenant_id, video_id):
                    logger.info("[HANDLER] Cancel requested for video_id=%s, skipping", video_id)
                    return "skip:cancel"
            except Exception as e:
                logger.debug("[HANDLER] is_cancel_requested check failed: %s", e)

        # processor 단계별 취소 확인용
        def _cancel_check() -> bool:
            try:
                from apps.support.video.redis_status_cache import is_cancel_requested
                return bool(tenant_id is not None and is_cancel_requested(tenant_id, video_id))
            except Exception:
                return False

        job["_cancel_check"] = _cancel_check

        logger.info("[HANDLER] Starting video processing video_id=%s job_id=%s fast_ack=%s", video_id, job_id, use_fast_ack)

        # Fast ACK 모드: DB try_claim으로 중복 방지 (SQS 메시지는 이미 delete됨)
        if use_fast_ack:
            if not self._repo.try_claim_video(video_id, worker_id):
                logger.info("CLAIM_FAILED_REQUEUE | video_id=%s", video_id)
                # 이미 FAST_ACK로 delete된 상태 → 즉시 reclaim으로 Reconciler 대상화 후 re-enqueue
                if self._repo.try_reclaim_video(video_id, force=True):
                    try:
                        from apps.support.video.models import Video
                        from apps.support.video.services.sqs_queue import VideoSQSQueue
                        video = Video.objects.select_related("session__lecture").get(pk=video_id)
                        if VideoSQSQueue().enqueue(video):
                            logger.info("CLAIM_FAILED_REQUEUE | video_id=%s re-enqueued", video_id)
                    except Exception as e:
                        logger.warning("CLAIM_FAILED_REQUEUE | video_id=%s enqueue failed: %s", video_id, e)
                return "skip:claim"
            logger.info("[HANDLER] JOB_CLAIMED video_id=%s worker_id=%s", video_id, worker_id)
        else:
            # Legacy: Redis idempotency + mark_processing
            if not self._idempotency.acquire_lock(job_id):
                logger.info("[HANDLER] Lock acquisition failed video_id=%s → NACK", video_id)
                return "lock_fail"
            logger.info("[HANDLER] Lock acquired, marking as PROCESSING video_id=%s", video_id)
            if not self._repo.mark_processing(video_id):
                logger.warning("[HANDLER] Cannot mark video %s as PROCESSING, skipping", video_id)
                self._idempotency.release_lock(job_id)
                return "skip:mark_processing"

        try:
            logger.info("[HANDLER] Starting process_fn video_id=%s", video_id)
            hls_path, duration = self._process_fn(job=job, cfg=cfg, progress=self._progress)
            logger.info("[HANDLER] process_fn completed video_id=%s hls_path=%s duration=%s", video_id, hls_path, duration)

            ok, reason = self._repo.complete_video(
                video_id=video_id,
                hls_path=hls_path,
                duration=duration,
            )
            if not ok:
                raise RuntimeError(f"Failed to complete video: {reason}")

            logger.info("Video processing completed: video_id=%s, duration=%s", video_id, duration)
            return "ok"

        except CancelledError:
            logger.info("[HANDLER] Processing cancelled (retry requested) video_id=%s", video_id)
            return "skip:cancel"
        except Exception as e:
            logger.exception("Video processing failed: video_id=%s, error=%s", video_id, e)
            self._repo.fail_video(video_id=video_id, reason=str(e)[:2000])
            return "failed"

        finally:
            if not use_fast_ack:
                self._idempotency.release_lock(job_id)


# Typo fix: idempotency -> self._idempotency
# Let me fix the handler - I used idempotency instead of self._idempotency