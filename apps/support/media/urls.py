# apps/support/media/urls.py

from django.urls import path, include
from rest_framework.routers import DefaultRouter

from .views import (
    VideoViewSet,
    VideoPermissionViewSet,
    VideoProgressViewSet,
    VideoPlaybackEventViewSet,
    VideoProcessingCompleteView,
)

router = DefaultRouter()

# ========================================================
# Admin / CRUD APIs
# ========================================================

router.register(r"videos", VideoViewSet, basename="videos")
router.register(r"video-permissions", VideoPermissionViewSet, basename="video-permissions")
router.register(r"video-progress", VideoProgressViewSet, basename="video-progress")
router.register(
    r"video-playback-events",
    VideoPlaybackEventViewSet,
    basename="video-playback-events",
)

urlpatterns = [
    path("", include(router.urls)),
]

# ========================================================
# Nested Video APIs (프론트 가독성용)
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

# ========================================================
# Playback APIs (Student, token-based)
# ========================================================

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

# ========================================================
# Internal APIs (Worker → API)
# ========================================================

urlpatterns += [
    path(
        "internal/videos/<int:video_id>/processing-complete/",
        VideoProcessingCompleteView.as_view(),
        name="media-video-processing-complete",
    ),
]
