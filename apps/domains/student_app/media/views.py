from typing import Any, Dict, Optional, Tuple

from django.http import Http404
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework.exceptions import PermissionDenied
from rest_framework import status

from apps.domains.student_app.permissions import IsStudent
from .serializers import (
    StudentVideoListItemSerializer,
    StudentVideoPlaybackSerializer,
)


# ======================================================
# 내부 유틸 (운영 안정성 우선)
# ======================================================

def _import_media_models():
    try:
        from apps.domains.media.models import Video  # type: ignore
    except Exception as e:
        raise RuntimeError(
            "[CRITICAL] apps.domains.media.models.Video import 실패"
        ) from e

    VideoPermission = None
    try:
        from apps.domains.media.models import VideoPermission  # type: ignore
        VideoPermission = VideoPermission
    except Exception:
        VideoPermission = None

    return Video, VideoPermission


def _get_student_enrollment_id(request) -> Optional[int]:
    q = request.query_params.get("enrollment")
    if q:
        try:
            return int(q)
        except Exception:
            return None

    user = request.user
    sp = getattr(user, "student_profile", None)
    if not sp:
        return None

    for key in ["enrollment_id", "current_enrollment_id", "enrollment"]:
        v = getattr(sp, key, None)
        if isinstance(v, int):
            return v

    enrollments = getattr(sp, "enrollments", None)
    try:
        if enrollments and hasattr(enrollments, "first"):
            first = enrollments.first()
            if first and hasattr(first, "id"):
                return int(first.id)
    except Exception:
        pass

    return None


def _pick_urls(video) -> Tuple[Optional[str], Optional[str]]:
    hls_url = getattr(video, "hls_url", None) or getattr(video, "hls_path", None)
    mp4_url = getattr(video, "mp4_url", None) or getattr(video, "file_url", None)
    return hls_url, mp4_url


def _effective_rule(video_permission_obj) -> str:
    if not video_permission_obj:
        return "free"

    rule = getattr(video_permission_obj, "rule", None) or getattr(
        video_permission_obj, "effective_rule", None
    )
    return rule if rule in ("free", "once", "blocked") else "free"


def _policy_from_video(video) -> Dict[str, Any]:
    return {
        "allow_skip": bool(getattr(video, "allow_skip", False)),
        "max_speed": float(getattr(video, "max_speed", 1.0) or 1.0),
        "show_watermark": bool(getattr(video, "show_watermark", True)),
    }


# ======================================================
# Views
# ======================================================

class StudentSessionVideoListView(APIView):
    """
    GET /student/media/sessions/{session_id}/videos/
    """

    permission_classes = [IsAuthenticated, IsStudent]

    def get(self, request, session_id: int):
        Video, VideoPermission = _import_media_models()
        enrollment_id = _get_student_enrollment_id(request)

        videos = Video.objects.filter(session_id=session_id).order_by("order", "id")

        items = []
        for v in videos:
            perm_obj = None
            if VideoPermission and enrollment_id:
                perm_obj = (
                    VideoPermission.objects
                    .filter(video_id=v.id, enrollment_id=enrollment_id)
                    .first()
                )

            thumb = getattr(v, "thumbnail_url", None) or getattr(v, "thumbnail", None)

            items.append({
                "id": int(v.id),
                "session_id": int(v.session_id),
                "title": str(v.title),
                "status": str(getattr(v, "status", "READY")),
                "thumbnail_url": thumb,
                **_policy_from_video(v),
                "effective_rule": _effective_rule(perm_obj),
            })

        return Response({
            "items": StudentVideoListItemSerializer(items, many=True).data
        })


class StudentVideoPlaybackView(APIView):
    """
    GET /student/media/videos/{video_id}/playback/
    """

    permission_classes = [IsAuthenticated, IsStudent]

    def get(self, request, video_id: int):
        Video, VideoPermission = _import_media_models()
        enrollment_id = _get_student_enrollment_id(request)

        try:
            video = Video.objects.get(id=video_id)
        except Video.DoesNotExist:
            raise Http404

        perm_obj = None
        if VideoPermission and enrollment_id:
            perm_obj = (
                VideoPermission.objects
                .filter(video_id=video.id, enrollment_id=enrollment_id)
                .first()
            )

        rule = _effective_rule(perm_obj)
        if rule == "blocked":
            raise PermissionDenied("이 영상은 시청이 제한되었습니다.")

        hls_url, mp4_url = _pick_urls(video)
        thumb = getattr(video, "thumbnail_url", None) or getattr(video, "thumbnail", None)

        payload = {
            "video": {
                "id": int(video.id),
                "session_id": int(video.session_id),
                "title": str(video.title),
                "status": str(getattr(video, "status", "READY")),
                "thumbnail_url": thumb,
                **_policy_from_video(video),
                "effective_rule": rule,
            },
            "hls_url": hls_url,
            "mp4_url": mp4_url,
            "policy": {
                **_policy_from_video(video),
                "effective_rule": rule,
            },
        }

        return Response(
            StudentVideoPlaybackSerializer(payload).data,
            status=status.HTTP_200_OK,
        )
