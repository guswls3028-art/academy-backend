# PATH: apps/support/video/urls_internal.py

from __future__ import annotations

from django.urls import path

from apps.support.video.views.internal_video_worker import (
    VideoWorkerClaimNextView,
    VideoWorkerCompleteView,
    VideoWorkerFailView,
)

from apps.support.video.views.internal_video_worker_heartbeat import (
    InternalVideoWorkerHeartbeatView,
)

urlpatterns = [
    # --------------------------------------------------
    # Worker job control
    # --------------------------------------------------
    path(
        "video-worker/next/",
        VideoWorkerClaimNextView.as_view(),
        name="video_worker_next",
    ),
    path(
        "video-worker/<int:video_id>/complete/",
        VideoWorkerCompleteView.as_view(),
        name="video_worker_complete",
    ),
    path(
        "video-worker/<int:video_id>/fail/",
        VideoWorkerFailView.as_view(),
        name="video_worker_fail",
    ),

    # --------------------------------------------------
    # Worker heartbeat (long-running protection)
    # --------------------------------------------------
    path(
        "video-worker/<int:video_id>/heartbeat/",
        InternalVideoWorkerHeartbeatView.as_view(),
        name="internal-video-worker-heartbeat",
    ),
]
