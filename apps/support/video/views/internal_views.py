# PATH: apps/support/video/views/internal_views.py

from __future__ import annotations

from rest_framework.views import APIView
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework import status

from apps.core.permissions import IsLambdaInternal
from apps.support.video.models import Video


class VideoProcessingCompleteView(APIView):
    """
    ✅ Legacy ACK endpoint (kept)

    기존 계약을 깨지 않기 위해 유지하되,
    "worker queue/claim" 같은 책임을 절대 섞지 않는다.

    POST /api/v1/videos/internal/videos/<video_id>/processing-complete/
    (프로젝트의 기존 URL 연결 방식에 맞춰 유지)

    body:
      {
        "hls_path": "...",
        "duration": 123
      }
    """

    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request, video_id: int):
        data = getattr(request, "data", None) or {}

        hls_path = data.get("hls_path")
        if not hls_path:
            return Response({"detail": "hls_path required"}, status=status.HTTP_400_BAD_REQUEST)

        duration = data.get("duration")
        try:
            duration_int = int(duration) if duration is not None else None
        except Exception:
            duration_int = None

        from academy.adapters.db.django import repositories_video as video_repo
        video = video_repo.video_get_by_id(int(video_id))
        if not video:
            return Response({"detail": "Not found"}, status=status.HTTP_404_NOT_FOUND)

        # 멱등
        if video.status == Video.Status.READY and bool(video.hls_path):
            return Response({"ok": True, "idempotent": True}, status=status.HTTP_200_OK)

        video.hls_path = str(hls_path)
        if duration_int is not None and duration_int >= 0:
            video.duration = duration_int
        video.status = Video.Status.READY

        # legacy complete는 lease 통제를 모를 수 있으므로 안전하게 lease 해제만 수행
        if hasattr(video, "leased_until"):
            video.leased_until = None
        if hasattr(video, "leased_by"):
            video.leased_by = ""

        update_fields = ["hls_path", "status"]
        if duration_int is not None and duration_int >= 0:
            update_fields.append("duration")
        if hasattr(video, "leased_until"):
            update_fields.append("leased_until")
        if hasattr(video, "leased_by"):
            update_fields.append("leased_by")

        video.save(update_fields=update_fields)

        return Response({"ok": True}, status=status.HTTP_200_OK)


class VideoBacklogCountView(APIView):
    """
    B1: BacklogCount for Video ASG TargetTracking. Redis only, no RDS.
    GET /api/v1/internal/video/backlog-count/ (or /internal/video/backlog/ if routed)
    Returns: {"backlog": int} — sum of tenant:{id}:video:backlog_count. Target <50ms for Lambda.
    """

    permission_classes = [IsLambdaInternal]
    authentication_classes = []

    def get(self, request):
        from apps.support.video.redis_status_cache import redis_get_video_backlog_total
        backlog = redis_get_video_backlog_total()
        return Response({"backlog": backlog})


class VideoBacklogScoreView(APIView):
    """
    B1: BacklogScore = SUM(QUEUED=>1, RETRY_WAIT=>2). CloudWatch Metric 교체용.
    GET /api/v1/internal/video/backlog-score/
    Returns: {"backlog_score": float}
    """

    permission_classes = [IsLambdaInternal]
    authentication_classes = []

    def get(self, request):
        from academy.adapters.db.django.repositories_video import job_compute_backlog_score
        score = job_compute_backlog_score()
        return Response({"backlog_score": score})


class VideoAsgInterruptStatusView(APIView):
    """
    queue_depth_lambda: interrupt 시 BacklogCount 퍼블리시 스킵 (scale-out runaway 방지).
    GET /api/v1/internal/video/asg-interrupt-status/
    Returns: {"interrupt": bool}
    """

    permission_classes = [IsLambdaInternal]
    authentication_classes = []

    def get(self, request):
        from apps.support.video.redis_status_cache import is_asg_interrupt
        return Response({"interrupt": is_asg_interrupt()})


class VideoDlqMarkDeadView(APIView):
    """
    DLQ State Sync Lambda: state별 분리 (scan_stuck와 race/경합 방지).
    - QUEUED, RETRY_WAIT: job_mark_dead(job_id)
    - RUNNING: DEAD로 바꾸지 않음, alert/log only
    - SUCCEEDED, DEAD: ignore
    POST /api/v1/internal/video/dlq-mark-dead/
    body: {"job_id": "uuid"}
    """

    permission_classes = [IsLambdaInternal]
    authentication_classes = []

    def post(self, request):
        import logging
        from apps.support.video.models import VideoTranscodeJob
        from academy.adapters.db.django.repositories_video import job_get_by_id, job_mark_dead

        logger = logging.getLogger(__name__)
        data = getattr(request, "data", None) or {}
        job_id = data.get("job_id")
        if not job_id:
            return Response({"detail": "job_id required"}, status=status.HTTP_400_BAD_REQUEST)
        job = job_get_by_id(str(job_id))
        if not job:
            return Response({"detail": "job not found"}, status=status.HTTP_404_NOT_FOUND)

        if job.state in (VideoTranscodeJob.State.SUCCEEDED, VideoTranscodeJob.State.DEAD):
            return Response({"ok": True, "skipped": "already_terminal", "state": job.state})

        if job.state == VideoTranscodeJob.State.RUNNING:
            logger.warning(
                "DLQ_RUNNING_ALERT | job_id=%s video_id=%s state=RUNNING — not marking DEAD (scan_stuck may recover)",
                job_id, job.video_id,
            )
            return Response({"ok": True, "skipped": "running_alert_only", "state": job.state})

        if job.state in (VideoTranscodeJob.State.QUEUED, VideoTranscodeJob.State.RETRY_WAIT):
            ok = job_mark_dead(str(job_id), error_code="DLQ", error_message="DLQ state sync marked dead")
            if ok:
                return Response({"ok": True})
            return Response({"detail": "job_mark_dead failed"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        # CANCELLED, FAILED 등: DEAD로 정리
        ok = job_mark_dead(str(job_id), error_code="DLQ", error_message="DLQ state sync marked dead")
        if ok:
            return Response({"ok": True})
        return Response({"detail": "job_mark_dead failed"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class VideoDeleteR2InternalView(APIView):
    """
    Lambda(SQS trigger): delete_r2 메시지 처리.
    POST /api/v1/internal/video/delete-r2/
    body: {"video_id": int, "file_key": str, "hls_prefix": str}
    """
    permission_classes = [IsLambdaInternal]
    authentication_classes = []

    def post(self, request):
        from apps.infrastructure.storage.r2 import delete_object_r2_video, delete_prefix_r2_video

        data = getattr(request, "data", None) or {}
        video_id = data.get("video_id")
        file_key = (data.get("file_key") or "").strip()
        hls_prefix = (data.get("hls_prefix") or "").strip()

        if not video_id:
            return Response({"detail": "video_id required"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            if file_key:
                delete_object_r2_video(key=file_key)
            if hls_prefix:
                delete_prefix_r2_video(prefix=hls_prefix)
            return Response({"ok": True, "video_id": video_id})
        except Exception as e:
            import logging
            logging.getLogger(__name__).exception("delete_r2 failed video_id=%s", video_id)
            return Response({"detail": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class VideoScanStuckView(APIView):
    """
    EventBridge Scheduled Lambda: scan_stuck_video_jobs 로직 실행.
    POST /api/v1/internal/video/scan-stuck/
    body: {"threshold": 3} (optional, minutes)
    """

    permission_classes = [IsLambdaInternal]
    authentication_classes = []

    def post(self, request):
        from django.utils import timezone
        from datetime import timedelta
        from apps.support.video.models import VideoTranscodeJob

        data = getattr(request, "data", None) or {}
        threshold_minutes = int(data.get("threshold", 3))
        cutoff = timezone.now() - timedelta(minutes=threshold_minutes)
        max_attempts = 5

        qs = VideoTranscodeJob.objects.filter(
            state=VideoTranscodeJob.State.RUNNING,
            last_heartbeat_at__lt=cutoff,
        ).order_by("id")

        recovered = 0
        dead = 0

        from academy.adapters.db.django.repositories_video import job_mark_dead

        for job in qs:
            attempt_after = job.attempt_count + 1
            if attempt_after >= max_attempts:
                job_mark_dead(
                    str(job.id),
                    error_code="STUCK_MAX_ATTEMPTS",
                    error_message=f"Stuck (no heartbeat for {threshold_minutes}min)",
                )
                dead += 1
            else:
                job.state = VideoTranscodeJob.State.RETRY_WAIT
                job.attempt_count = attempt_after
                job.locked_by = ""
                job.locked_until = None
                job.save(update_fields=["state", "attempt_count", "locked_by", "locked_until", "updated_at"])
                recovered += 1

        return Response({"recovered": recovered, "dead": dead})
