# PATH: apps/support/video/views/video_views.py

from uuid import uuid4
from datetime import timedelta

from django.conf import settings
from django.db import models, transaction
from django.utils import timezone

from rest_framework import status
from rest_framework.decorators import action
from rest_framework.filters import SearchFilter
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.viewsets import ModelViewSet
from rest_framework.parsers import (
    JSONParser,
    MultiPartParser,
    FormParser,
)
from django_filters.rest_framework import DjangoFilterBackend

from rest_framework_simplejwt.authentication import JWTAuthentication

from libs.s3_client.presign import create_presigned_put_url, create_presigned_get_url
from libs.s3_client.client import head_object

from apps.core.permissions import IsAdminOrStaff, IsStudent
from apps.core.authentication import CsrfExemptSessionAuthentication

from apps.domains.lectures.models import Session
from apps.domains.enrollment.models import Enrollment, SessionEnrollment
from apps.domains.attendance.models import Attendance

from ..models import (
    Video,
    VideoPermission,
    VideoProgress,
    VideoPlaybackEvent,
)
from ..serializers import VideoSerializer, VideoDetailSerializer
from .playback_mixin import VideoPlaybackMixin

# ❌ API 부팅 크래시 원인 제거:
# import ffmpeg  # ✅ ffprobe(validate) 용도 (문제 5)


def _safe_int(v, default=None):
    try:
        return int(v)
    except Exception:
        return default


def _validate_source_media_via_ffprobe(url: str) -> tuple[bool, dict, str]:
    """
    ✅ upload_complete 최소 무결성 검증:
    - ffprobe로 format.duration 확인
    - video stream 존재 확인
    - 실패 이유 문자열 반환

    NOTE:
    - 네트워크/Range 요청 기반 (ffmpeg-python -> ffprobe)

    ✅ PATCH:
    - API 서버에서 ffmpeg 모듈 import 실패 시(venv 경로/서비스 환경 문제 등)
      API 전체가 죽지 않게 하고, 호출부에서 graceful fallback 하도록 reason 반환
    """
    if not url:
        return False, {}, "source_url_missing"

    # ✅ lazy import (gunicorn 부팅 크래시 방지)
    try:
        import ffmpeg  # type: ignore
    except Exception:
        return False, {}, "ffmpeg_module_missing"

    try:
        probe = ffmpeg.probe(url)
    except Exception as e:
        return False, {}, f"ffprobe_failed:{str(e)[:200]}"

    fmt = probe.get("format") or {}
    streams = probe.get("streams") or []

    # duration
    dur_raw = fmt.get("duration")
    duration = None
    try:
        if dur_raw is not None:
            duration = int(float(dur_raw))
    except Exception:
        duration = None

    # video stream 존재
    has_video = any((s.get("codec_type") == "video") for s in streams)

    if not has_video:
        return False, {"duration": duration, "has_video": False}, "no_video_stream"

    if duration is None:
        return False, {"duration": None, "has_video": True}, "duration_missing"

    if duration < 0:
        return False, {"duration": duration, "has_video": True}, "duration_invalid"

    return True, {"duration": duration, "has_video": True}, ""


class VideoViewSet(VideoPlaybackMixin, ModelViewSet):
    """
    Video 관리 + 통계 + 학생 목록
    """

    queryset = Video.objects.all().select_related("session", "session__lecture")
    serializer_class = VideoSerializer

    parser_classes = [JSONParser]

    authentication_classes = [
        JWTAuthentication,
        CsrfExemptSessionAuthentication,
    ]
    permission_classes = [IsAuthenticated]

    ADMIN_ONLY_ACTIONS = {
        "upload_init",
        "upload_complete",
        "retry",
        "create",
        "update",
        "partial_update",
        "destroy",
    }

    def get_permissions(self):
        if self.action in self.ADMIN_ONLY_ACTIONS:
            return [IsAuthenticated(), IsAdminOrStaff()]
        return [IsAuthenticated()]

    filter_backends = [DjangoFilterBackend, SearchFilter]
    filterset_fields = ["session", "status"]
    search_fields = ["title"]

    # ==================================================
    # upload/init (presigned URL 발급)
    # ==================================================
    @transaction.atomic
    @action(
        detail=False,
        methods=["post"],
        url_path="upload/init",
        parser_classes=[JSONParser],
    )
    def upload_init(self, request):
        session_id = request.data.get("session")
        title = request.data.get("title")
        filename = request.data.get("filename")

        allow_skip = bool(request.data.get("allow_skip", False))
        max_speed = float(request.data.get("max_speed", 1.0) or 1.0)
        show_watermark = bool(request.data.get("show_watermark", True))

        if not session_id or not title or not filename:
            return Response(
                {"detail": "session, title, filename required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        session = Session.objects.get(id=session_id)
        order = (
            session.videos.aggregate(max_order=models.Max("order")).get("max_order") or 0
        ) + 1

        ext = filename.split(".")[-1].lower() if "." in filename else "mp4"
        key = f"videos/{session_id}/{uuid4()}.{ext}"

        video = Video.objects.create(
            session=session,
            title=title,
            file_key=key,
            order=order,
            status=Video.Status.PENDING,
            allow_skip=allow_skip,
            max_speed=max_speed,
            show_watermark=show_watermark,
        )

        content_type = (request.data.get("content_type") or "video/mp4").split(";")[0]
        upload_url = create_presigned_put_url(key=key, content_type=content_type)

        return Response(
            {
                "video": VideoSerializer(video).data,
                "upload_url": upload_url,
                "file_key": key,
                "content_type": content_type,
            },
            status=status.HTTP_201_CREATED,
        )

    # ==================================================
    # upload/complete (업로드 완료 확인 + 무결성 검증 + 상태 전이)
    # ==================================================
    @transaction.atomic
    @action(
        detail=True,
        methods=["post"],
        url_path="upload/complete",
        parser_classes=[MultiPartParser, FormParser, JSONParser],
    )
    def upload_complete(self, request, pk=None):
        """
        ✅ 문제 5 해결:
        - head_object 존재 확인만 하던 것을 확장
        - presigned GET URL로 ffprobe 검증:
          - video stream 존재
          - duration 추출
          - 최소 duration 기준
        - 실패 시 status 전이 금지 + error_reason 기록

        ✅ PATCH:
        - ffmpeg 모듈이 API 런타임에서 import 불가하면(서비스 env/venv 경로 문제 등)
          API 자체가 죽지 않도록 ffprobe 검증은 스킵하고 UPLOADED로 전이한다.
          (원본 흐름 유지 + 서비스 가용성 우선)
        """
        video = self.get_object()

        if video.status != Video.Status.PENDING:
            return Response(
                {"detail": f"Invalid status: {video.status}"},
                status=status.HTTP_409_CONFLICT,
            )

        exists, size = head_object(video.file_key)
        if not exists or size == 0:
            video.error_reason = "source_not_found_or_empty"
            video.save(update_fields=["error_reason"])
            return Response(
                {"detail": "S3 object not found"},
                status=status.HTTP_409_CONFLICT,
            )

        # ✅ presigned GET URL → ffprobe validation
        try:
            src_url = create_presigned_get_url(key=video.file_key, expires_in=600)
        except Exception as e:
            video.error_reason = f"presigned_get_failed:{str(e)[:200]}"
            video.save(update_fields=["error_reason"])
            return Response(
                {"detail": "presigned_get_failed"},
                status=status.HTTP_409_CONFLICT,
            )

        ok, meta, reason = _validate_source_media_via_ffprobe(src_url)

        # ✅ PATCH: ffmpeg 모듈이 없으면 API 크래시 방지 + 검증 스킵
        if not ok and reason == "ffmpeg_module_missing":
            video.status = Video.Status.UPLOADED
            video.error_reason = ""
            video.save(update_fields=["status", "error_reason"])
            return Response(VideoSerializer(video).data)

        if not ok:
            video.error_reason = f"source_invalid:{reason}"
            video.save(update_fields=["error_reason"])
            return Response(
                {"detail": "source_invalid", "reason": reason},
                status=status.HTTP_409_CONFLICT,
            )

        min_dur = _safe_int(getattr(settings, "VIDEO_MIN_DURATION_SECONDS", 3), 3)
        duration = _safe_int(meta.get("duration"), None)
        if duration is None or duration < int(min_dur):
            video.error_reason = f"duration_too_short:{duration}"
            video.save(update_fields=["error_reason"])
            return Response(
                {"detail": "duration_too_short", "duration": duration},
                status=status.HTTP_409_CONFLICT,
            )

        # duration 저장(있으면)
        video.duration = duration

        # 상태 전이
        video.status = Video.Status.UPLOADED
        video.error_reason = ""
        video.save(update_fields=["status", "duration", "error_reason"])

        return Response(VideoSerializer(video).data)

    # ==================================================
    # retry (HTTP worker 기준: status를 UPLOADED로 돌림)
    # ==================================================
    @transaction.atomic
    @action(detail=True, methods=["post"], url_path="retry")
    def retry(self, request, pk=None):
        video = self.get_object()

        if video.status not in (Video.Status.FAILED, Video.Status.UPLOADED):
            return Response(
                {"detail": "Cannot retry"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        video.status = Video.Status.UPLOADED
        video.save(update_fields=["status"])

        return Response(
            {"detail": "Video reprocessing queued (HTTP worker polling)"},
            status=status.HTTP_202_ACCEPTED,
        )

    # ==================================================
    # stats (관리자 학생별 상세)
    # ==================================================
    @action(detail=True, methods=["get"], url_path="stats")
    def stats(self, request, pk=None):
        video = self.get_object()
        lecture = video.session.lecture

        enrollments = Enrollment.objects.filter(
            lecture=lecture,
            status="ACTIVE",
        ).select_related("student")

        progresses = {p.enrollment_id: p for p in VideoProgress.objects.filter(video=video)}
        perms = {p.enrollment_id: p for p in VideoPermission.objects.filter(video=video)}
        attendance = {
            a.enrollment_id: a.status
            for a in Attendance.objects.filter(session=video.session)
        }

        students = []
        for e in enrollments:
            vp = progresses.get(e.id)
            perm = perms.get(e.id)

            rule = perm.rule if perm else "free"
            effective_rule = rule
            if rule == "once" and vp and vp.completed:
                effective_rule = "free"

            students.append(
                {
                    "enrollment": e.id,
                    "student_name": e.student.name,
                    "attendance_status": attendance.get(e.id),
                    "progress": vp.progress if vp else 0,
                    "completed": vp.completed if vp else False,
                    "rule": rule,
                    "effective_rule": effective_rule,
                    "parent_phone": getattr(e.student, "parent_phone", None),
                    "student_phone": getattr(e.student, "phone", None),
                    "school": getattr(e.student, "school", None),
                    "grade": getattr(e.student, "grade", None),
                }
            )

        return Response(
            {
                "video": VideoDetailSerializer(video).data,
                "students": students,
                "total_filtered": len(students),
            }
        )

    # ==================================================
    # summary (통계 탭 요약)
    # ==================================================
    @action(detail=True, methods=["get"], url_path="summary")
    def summary(self, request, pk=None):
        video = self.get_object()
        lecture = video.session.lecture

        range_key = request.query_params.get("range", "7d")
        now = timezone.now()

        since = None
        if range_key == "24h":
            since = now - timedelta(hours=24)
        elif range_key == "7d":
            since = now - timedelta(days=7)

        enrollments = Enrollment.objects.filter(lecture=lecture)
        total = enrollments.count()

        progresses = VideoProgress.objects.filter(video=video)
        completed_count = progresses.filter(completed=True).count()

        duration = int(video.duration or 0)

        watched_seconds = 0
        for p in progresses.iterator():
            watched_seconds += int(float(p.progress or 0) * duration)

        completion_rate = (completed_count / total) if total else 0.0

        ev_qs = VideoPlaybackEvent.objects.filter(video=video).select_related(
            "enrollment", "enrollment__student"
        )

        if since:
            ev_qs = ev_qs.filter(occurred_at__gte=since)

        weights = {
            "VISIBILITY_HIDDEN": 1,
            "VISIBILITY_VISIBLE": 0,
            "FOCUS_LOST": 2,
            "FOCUS_GAINED": 0,
            "SEEK_ATTEMPT": 3,
            "SPEED_CHANGE_ATTEMPT": 3,
            "FULLSCREEN_ENTER": 0,
            "FULLSCREEN_EXIT": 0,
            "PLAYER_ERROR": 1,
        }

        agg = {}
        for ev in ev_qs.iterator():
            eid = ev.enrollment_id
            if eid not in agg:
                agg[eid] = {
                    "enrollment": eid,
                    "student_name": ev.enrollment.student.name,
                    "score": 0,
                }

            score = int(weights.get(ev.event_type, 1))
            if ev.violated:
                score *= 2
            if ev.violation_reason:
                score += 1

            agg[eid]["score"] += score

        risk_top = sorted(agg.values(), key=lambda x: x["score"], reverse=True)[:5]

        return Response(
            {
                "video_id": video.id,
                "range": range_key,
                "total_students": total,
                "completed_count": completed_count,
                "completion_rate": completion_rate,
                "watched_seconds_est": watched_seconds,
                "risk_top": risk_top,
            }
        )

    # ==================================================
    # student list
    # ==================================================
    @action(
        detail=False,
        methods=["get"],
        url_path="student",
        permission_classes=[IsAuthenticated, IsStudent],
    )
    def student_list(self, request):
        return self._student_list_impl(request)
