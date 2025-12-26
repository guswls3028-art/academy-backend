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

from .views import PlaybackSessionView

urlpatterns += [
    path("playback/sessions/", PlaybackSessionView.as_view(), name="media-playback-session"),
]
