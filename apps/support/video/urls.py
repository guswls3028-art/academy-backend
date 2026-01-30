# PATH: apps/support/video/urls.py

from django.urls import path, include
from rest_framework.routers import DefaultRouter

from .views import (
    VideoViewSet,
    VideoPermissionViewSet,
    VideoProgressViewSet,
    VideoPlaybackEventViewSet,
    VideoProcessingCompleteView,
    VideoPolicyImpactAPIView,
)

from .views.achievement_views import VideoAchievementView
from .views.playback_views import (
    PlaybackStartView,
    PlaybackRefreshView,
    PlaybackHeartbeatView,
    PlaybackEndView,
    PlaybackEventBatchView,
)

# ========================================================
# Router
# ========================================================

router = DefaultRouter()
router.register(r"videos", VideoViewSet, basename="videos")
router.register(r"video-permissions", VideoPermissionViewSet, basename="video-permissions")
router.register(r"video-progress", VideoProgressViewSet, basename="video-progress")
router.register(r"video-playback-events", VideoPlaybackEventViewSet, basename="video-playback-events")

# ========================================================
# urlpatterns (선언 먼저!)
# ========================================================

urlpatterns = [
    path("", include(router.urls)),
]

# ========================================================
# Nested / Extra APIs
# ========================================================

video_detail = VideoViewSet.as_view({"get": "retrieve"})
video_stats = VideoViewSet.as_view({"get": "stats"})

urlpatterns += [
    path(
        "lectures/<int:lecture_id>/sessions/<int:session_id>/videos/<int:pk>/",
        video_detail,
        name="video-video-detail-nested",
    ),
    path(
        "lectures/<int:lecture_id>/sessions/<int:session_id>/videos/<int:pk>/stats/",
        video_stats,
        name="video-video-stats-nested",
    ),
    path(
        "videos/<int:video_id>/achievement/",
        VideoAchievementView.as_view(),
        name="video-video-achievement",
    ),
]

# ========================================================
# Playback APIs (Student)
# ========================================================

urlpatterns += [
    path("playback/start/", PlaybackStartView.as_view()),
    path("playback/refresh/", PlaybackRefreshView.as_view()),
    path("playback/heartbeat/", PlaybackHeartbeatView.as_view()),
    path("playback/end/", PlaybackEndView.as_view()),
    path("playback/events/", PlaybackEventBatchView.as_view()),
]

# ========================================================
# Internal (Legacy ACK - kept)
# ========================================================

urlpatterns += [
    path(
        "internal/videos/<int:video_id>/processing-complete/",
        VideoProcessingCompleteView.as_view(),
        name="video-video-processing-complete",
    ),
]

# ========================================================
# Policy Impact (Admin preview)
# ========================================================

urlpatterns += [
    path(
        "videos/<int:video_id>/policy-impact/",
        VideoPolicyImpactAPIView.as_view(),
        name="video-video-policy-impact",
    ),
]
