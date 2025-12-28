# apps/support/media/views/video_views.py

from uuid import uuid4

from django.conf import settings
from django.db import models, transaction
from django.utils import timezone

from rest_framework import status
from rest_framework.decorators import action
from rest_framework.filters import SearchFilter
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.viewsets import ModelViewSet
from rest_framework.parsers import JSONParser
from django_filters.rest_framework import DjangoFilterBackend

from rest_framework_simplejwt.authentication import JWTAuthentication

from libs.s3_client.presign import create_presigned_put_url
from libs.s3_client.client import head_object

from apps.core.permissions import IsAdminOrStaff, IsStudent
from apps.core.authentication import CsrfExemptSessionAuthentication

from apps.domains.lectures.models import Session
from apps.domains.enrollment.models import Enrollment, SessionEnrollment
from apps.domains.attendance.models import Attendance

from apps.shared.tasks.media import process_video_media

from ..models import Video, VideoPermission, VideoProgress
from ..serializers import VideoSerializer, VideoDetailSerializer
from .playback_mixin import VideoPlaybackMixin


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
    # upload/init
    # ==================================================
    @transaction.atomic
    @action(detail=False, methods=["post"], url_path="upload/init")
    def upload_init(self, request):
        session_id = request.data.get("session")
        title = request.data.get("title")
        filename = request.data.get("filename")

        allow_skip = bool(request.data.get("allow_skip", False))
        max_speed = float(request.data.get("max_speed", 1.0) or 1.0)
        show_watermark = bool(request.data.get("show_watermark", True))

        if not session_id or not title or not filename:
            return Response({"detail": "session, title, filename required"}, status=400)

        session = Session.objects.get(id=session_id)
        order = (session.videos.aggregate(max_order=models.Max("order")).get("max_order") or 0) + 1

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
            status=201,
        )

    # ==================================================
    # upload/complete
    # ==================================================
    @transaction.atomic
    @action(detail=True, methods=["post"], url_path="upload/complete")
    def upload_complete(self, request, pk=None):
        video = self.get_object()

        if video.status != Video.Status.PENDING:
            return Response({"detail": f"Invalid status: {video.status}"}, status=409)

        exists, size = head_object(video.file_key)
        if not exists or size == 0:
            return Response({"detail": "S3 object not found"}, status=409)

        video.status = Video.Status.UPLOADED
        video.save(update_fields=["status"])

        process_video_media.delay(video.id)
        return Response(VideoSerializer(video).data)

    # ==================================================
    # retry
    # ==================================================
    @transaction.atomic
    @action(detail=True, methods=["post"], url_path="retry")
    def retry(self, request, pk=None):
        video = self.get_object()

        if video.status not in (Video.Status.FAILED, Video.Status.UPLOADED):
            return Response({"detail": "Cannot retry"}, status=400)

        process_video_media.delay(video.id)
        return Response({"detail": "Video reprocessing started"}, status=202)

    # ==================================================
    # stats
    # ==================================================
    @action(detail=True, methods=["get"], url_path="stats")
    def stats(self, request, pk=None):
        video = self.get_object()
        lecture = video.session.lecture

        enrollments = Enrollment.objects.filter(
            lecture=lecture,
            status="ACTIVE",
        ).select_related("student")

        progresses = {
            p.enrollment_id: p
            for p in VideoProgress.objects.filter(video=video)
        }
        perms = {
            p.enrollment_id: p
            for p in VideoPermission.objects.filter(video=video)
        }
        attendance = {
            a.enrollment_id: a.status
            for a in Attendance.objects.filter(session=video.session)
        }

        students = []
        for e in enrollments:
            vp = progresses.get(e.id)
            perm = perms.get(e.id)

            rule = perm.rule if perm else "free"

            students.append({
                # 🔥 관리자 권한 UI 핵심
                "enrollment": e.id,
                "student_name": e.student.name,
                "attendance_status": attendance.get(e.id),

                # 진행 정보
                "progress": vp.progress if vp else 0,
                "completed": vp.completed if vp else False,

                # 권한
                "effective_rule": rule,

                # 표시용 메타
                "parent_phone": getattr(e.student, "parent_phone", None),
                "student_phone": getattr(e.student, "phone", None),
                "school": getattr(e.student, "school", None),
                "grade": getattr(e.student, "grade", None),
            })

        return Response({
            "video": VideoDetailSerializer(video).data,
            "students": students,
            "total_filtered": len(students),
        })

    # ==================================================
    # student list
    # ==================================================
    @action(detail=False, methods=["get"], url_path="student", permission_classes=[IsAuthenticated, IsStudent])
    def student_list(self, request):
        return self._student_list_impl(request)
