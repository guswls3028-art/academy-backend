# app/support/media/urls.py

from django.urls import path, include
from rest_framework.routers import DefaultRouter


from .views import VideoProcessingCompleteView

from .views import (
    VideoViewSet,
    VideoPermissionViewSet,
    VideoProgressViewSet,
)

router = DefaultRouter()

# ========================================================
# Video
# ========================================================

router.register(r"videos", VideoViewSet, basename="videos")
router.register(r"video-permissions", VideoPermissionViewSet, basename="video-permissions")
router.register(r"video-progress", VideoProgressViewSet, basename="video-progress")

urlpatterns = [
    path("", include(router.urls)),
]

# ========================================================
# Nested / 상세 Video API
# ========================================================

video_detail = VideoViewSet.as_view({"get": "retrieve"})
video_stats = VideoViewSet.as_view({"get": "stats"})

urlpatterns += [
    path(
        "lectures/<int:lecture_id>/sessions/<int:session_id>/videos/<int:pk>/",
        video_detail,
        name="media-video-detail-nested",
    ),
    path(
        "lectures/<int:lecture_id>/sessions/<int:session_id>/videos/<int:pk>/stats/",
        video_stats,
        name="media-video-stats-nested",
    ),
]

# 대충 붙이기

# playback APIs (token-based)

from .views.playback_views import (
    PlaybackStartView,
    PlaybackRefreshView,
    PlaybackHeartbeatView,
    PlaybackEndView,
    PlaybackEventBatchView,
)

urlpatterns += [
    path("playback/start/", PlaybackStartView.as_view()),
    path("playback/refresh/", PlaybackRefreshView.as_view()),
    path("playback/heartbeat/", PlaybackHeartbeatView.as_view()),
    path("playback/end/", PlaybackEndView.as_view()),
    path("playback/events/", PlaybackEventBatchView.as_view()),
]
