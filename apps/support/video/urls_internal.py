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
    # Worker job control (SSOT)
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
    # Worker heartbeat (lease extension)
    # --------------------------------------------------
    path(
        "video-worker/<int:video_id>/heartbeat/",
        InternalVideoWorkerHeartbeatView.as_view(),
        name="internal_video_worker_heartbeat",
    ),

    # ==================================================
    # ✅ Compatibility Alias (경로 혼동 흡수)
    #
    # 일부 워커/스크립트가 /internal/video-worker/... 로 호출하는 경우가 있어
    # 동일 View로 라우팅되는 alias를 제공한다.
    #
    # - /api/v1/internal/ 아래에 include되면:
    #     /api/v1/internal/internal/video-worker/... 가 될 수 있으니
    #     "이 파일이 어디에 include되는지"에 관계없이
    #     최소 한쪽은 반드시 맞도록 중복 엔드포인트를 둔다.
    #
    # - 만약 프로젝트에서 이미 "/internal/" prefix로 별도 mount 중이면:
    #     /internal/video-worker/... 로 바로 매칭된다.
    # ==================================================
    path(
        "internal/video-worker/next/",
        VideoWorkerClaimNextView.as_view(),
        name="video_worker_next_alias_internal",
    ),
    path(
        "internal/video-worker/<int:video_id>/complete/",
        VideoWorkerCompleteView.as_view(),
        name="video_worker_complete_alias_internal",
    ),
    path(
        "internal/video-worker/<int:video_id>/fail/",
        VideoWorkerFailView.as_view(),
        name="video_worker_fail_alias_internal",
    ),
    path(
        "internal/video-worker/<int:video_id>/heartbeat/",
        InternalVideoWorkerHeartbeatView.as_view(),
        name="internal_video_worker_heartbeat_alias_internal",
    ),
]
