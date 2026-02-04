# PATH: apps/support/video/views/video_views.py

import logging
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

# 로거 설정
logger = logging.getLogger(__name__)

# ==================================================
# utils
# ==================================================
def _safe_int(v, default=None):
    try:
        return int(v)
    except Exception:
        return default


def _validate_source_media_via_ffprobe(url: str) -> tuple[bool, dict, str]:
    """
    upload_complete 최소 무결성 검증
    """
    if not url:
        return False, {}, "source_url_missing"

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

    dur_raw = fmt.get("duration")
    duration = None
    try:
        if dur_raw is not None:
            duration = int(float(dur_raw))
    except Exception:
        duration = None

    has_video = any((s.get("codec_type") == "video") for s in streams)

    if not has_video:
        return False, {"duration": duration, "has_video": False}, "no_video_stream"

    if duration is None:
        return False, {"duration": None, "has_video": True}, "duration_missing"

    if duration < 0:
        return False, {"duration": duration, "has_video": True}, "duration_invalid"

    return True, {"duration": duration, "has_video": True}, ""


def _try_start_video_worker_instance(retry_count=0) -> None:
    """
    upload_complete 시점에 video-worker EC2 인스턴스 자동 기동 (로그 분석 강화)
    - 수정사항: 중지 중(Stopping) 상태 대응을 위해 10초 간격으로 최대 120초(12회) 재시도 수행
    """
    import threading
    
    instance_id = getattr(settings, "VIDEO_WORKER_INSTANCE_ID", None) or ""
    region = (
        getattr(settings, "AWS_REGION", None)
        or getattr(settings, "AWS_DEFAULT_REGION", None)
        or "ap-northeast-2"
    )

    if not instance_id:
        logger.error("[EC2-START] VIDEO_WORKER_INSTANCE_ID 설정이 없습니다.")
        return

    # 재시도 설정: 10초 간격으로 최대 12번 (총 120초)
    MAX_RETRIES = 12
    RETRY_INTERVAL = 10

    try:
        import boto3  # type: ignore
        ec2 = boto3.client("ec2", region_name=region)
        
        # 기동 시도 및 응답 로그 기록
        response = ec2.start_instances(InstanceIds=[str(instance_id)])
        logger.info(f"[EC2-START] 성공: {instance_id} 기동 명령 전송 (시도 {retry_count + 1}회). 응답: {response.get('StartingInstances')}")
        
    except Exception as e:
        error_str = str(e)
        # 인스턴스가 중지 중(Stopping)이거나 일시적인 상태 오류인 경우
        if "IncorrectInstanceState" in error_str or "InstanceInterrupted" in error_str:
            if retry_count < MAX_RETRIES:
                logger.warning(f"[EC2-START] 인스턴스가 준비되지 않음. {RETRY_INTERVAL}초 후 재시도... ({retry_count + 1}/{MAX_RETRIES})")
                threading.Timer(
                    RETRY_INTERVAL, 
                    _try_start_video_worker_instance, 
                    args=[retry_count + 1]
                ).start()
            else:
                logger.error(f"[EC2-START] 최대 재시도(120초) 초과. 기동 실패: {error_str}")
        else:
            # 실패 시 구체적인 에러 메시지 로깅
            logger.error(f"[EC2-START] 실패: {error_str}", exc_info=True)


def _try_start_video_worker_instance_after_job_creation() -> None:
    """
    job 생성 이후 EC2 인스턴스 활성화:
    - 쓰레드 딜레이 제거: 요청 응답 전/프로세스 종료 전 안정적인 호출 보장
    """
    _try_start_video_worker_instance()


# ==================================================
# ViewSet
# ==================================================
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

        session = Session.objects.select_related("lecture", "lecture__tenant").get(id=session_id)
        tenant_code = session.lecture.tenant.code
        order = (
            session.videos.aggregate(max_order=models.Max("order")).get("max_order") or 0
        ) + 1

        ext = filename.split(".")[-1].lower() if "." in filename else "mp4"
        key = f"videos/{tenant_code}/{session_id}/{uuid4()}.{ext}"

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
    # upload/complete
    # ==================================================
    @transaction.atomic
    @action(
        detail=True,
        methods=["post"],
        url_path="upload/complete",
        parser_classes=[MultiPartParser, FormParser, JSONParser],
    )
    def upload_complete(self, request, pk=None):
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

        if not ok and reason == "ffmpeg_module_missing":
            video.status = Video.Status.UPLOADED
            video.error_reason = ""
            video.save(update_fields=["status", "error_reason"])

            transaction.on_commit(_try_start_video_worker_instance_after_job_creation)
            return Response(VideoSerializer(video).data)

        min_dur = _safe_int(getattr(settings, "VIDEO_MIN_DURATION_SECONDS", 3), 3)
        duration = _safe_int(meta.get("duration"), None)

        if duration is not None and duration < int(min_dur):
            video.duration = duration
            video.status = Video.Status.UPLOADED
            video.error_reason = ""
            video.save(update_fields=["status", "duration", "error_reason"])

            transaction.on_commit(_try_start_video_worker_instance_after_job_creation)
            return Response(VideoSerializer(video).data)

        video.duration = duration
        video.status = Video.Status.UPLOADED
        video.error_reason = ""
        video.save(update_fields=["status", "duration", "error_reason"])

        transaction.on_commit(_try_start_video_worker_instance_after_job_creation)
        return Response(VideoSerializer(video).data)

    # ==================================================
    # retry
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

        transaction.on_commit(_try_start_video_worker_instance_after_job_creation)
        return Response(
            {"detail": "Video reprocessing queued (HTTP worker polling)"},
            status=status.HTTP_202_ACCEPTED,
        )

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
    # summary
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

        risk_top = sorted(
            agg.values(),
            key=lambda x: x["score"],
            reverse=True,
        )[:5]

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