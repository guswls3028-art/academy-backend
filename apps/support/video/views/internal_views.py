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
    B1: BacklogCount (Job 기반: QUEUED + RETRY_WAIT, RUNNING 제외) for Video ASG TargetTracking.
    GET /api/v1/internal/video/backlog-count/
    Returns: {"backlog": int}
    queue_depth_lambda가 1분마다 X-Internal-Key 헤더로 호출.
    """

    permission_classes = [IsLambdaInternal]
    authentication_classes = []

    def get(self, request):
        from academy.adapters.db.django.repositories_video import job_count_backlog
        backlog = job_count_backlog()
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


class VideoDlqMarkDeadView(APIView):
    """
    DLQ State Sync Lambda: job_id로 job_mark_dead 호출.
    Job.state NOT IN (SUCCEEDED, DEAD) 일 때만 수행 (state reconciliation).
    POST /api/v1/internal/video/dlq-mark-dead/
    body: {"job_id": "uuid"}
    """

    permission_classes = [IsLambdaInternal]
    authentication_classes = []

    def post(self, request):
        from apps.support.video.models import VideoTranscodeJob
        from academy.adapters.db.django.repositories_video import job_get_by_id, job_mark_dead

        data = getattr(request, "data", None) or {}
        job_id = data.get("job_id")
        if not job_id:
            return Response({"detail": "job_id required"}, status=status.HTTP_400_BAD_REQUEST)
        job = job_get_by_id(str(job_id))
        if not job:
            return Response({"detail": "job not found"}, status=status.HTTP_404_NOT_FOUND)
        if job.state in (VideoTranscodeJob.State.SUCCEEDED, VideoTranscodeJob.State.DEAD):
            return Response({"ok": True, "skipped": "already_terminal", "state": job.state})
        ok = job_mark_dead(str(job_id), error_code="DLQ", error_message="DLQ state sync marked dead")
        if ok:
            return Response({"ok": True})
        return Response({"detail": "job_mark_dead failed"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


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
