# PATH: apps/support/video/views/video_views.py

import logging
from uuid import uuid4
from datetime import timedelta

from django.conf import settings
from django.db import models, transaction
from django.utils import timezone

from rest_framework import status
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
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


from apps.core.r2_paths import video_raw_key, video_hls_prefix
from apps.core.permissions import IsStudent, TenantResolvedAndStaff
from apps.core.authentication import CsrfExemptSessionAuthentication

from apps.domains.lectures.models import Lecture, Session
from apps.domains.enrollment.models import Enrollment, SessionEnrollment
from apps.domains.attendance.models import Attendance

from academy.adapters.db.django import repositories_video as video_repo
from ..models import (
    Video,
    VideoAccess,
    VideoProgress,
    VideoPlaybackEvent,
    VideoFolder,
)
from ..serializers import VideoSerializer, VideoDetailSerializer, VideoFolderSerializer
from ..services.sqs_queue import VideoSQSQueue
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


# ==================================================
# ✅ EC2 자동 시작 로직 제거됨 (SQS 기반 아키텍처로 전환)
#
# SQS 기반 아키텍처에서는:
# - Worker는 ECS/Fargate에서 자동으로 관리됨
# - 작업이 SQS에 있으면 Worker가 자동으로 처리
# - EC2 인스턴스 수동 관리 불필요
# ==================================================


# ==================================================
# ViewSet
# ==================================================
class VideoViewSet(VideoPlaybackMixin, ModelViewSet):
    """
    Video 관리 + 통계 + 학생 목록
    """

    queryset = video_repo.get_video_queryset_with_relations()
    serializer_class = VideoSerializer

    parser_classes = [JSONParser]

    authentication_classes = [
        JWTAuthentication,
        CsrfExemptSessionAuthentication,
    ]
    permission_classes = [IsAuthenticated]

    # 테넌트 스태프(owner/admin/staff/teacher)만 허용 — Django is_staff 없어도 오너·원장 업로드 가능
    STAFF_ACTIONS = {
        "upload_init",
        "upload_complete",
        "retry",
        "create",
        "update",
        "partial_update",
        "destroy",
        "public_session",
        "list_folders",
        "create_folder",
        "delete_folder",
    }

    def get_permissions(self):
        if self.action in self.STAFF_ACTIONS:
            return [IsAuthenticated(), TenantResolvedAndStaff()]
        return [IsAuthenticated()]

    filter_backends = [DjangoFilterBackend, SearchFilter]
    filterset_fields = ["session", "status", "folder"]
    search_fields = ["title"]

    def perform_destroy(self, instance):
        """
        영상 삭제: 현재 Job DEAD 처리 -> DB 삭제 -> R2 정리는 SQS로 워커 위임.
        Worker는 job 없으면(삭제된 영상) 메시지 consume 후 스킵.
        """
        video = video_repo.get_video_by_pk_with_relations(instance.pk)
        tenant_id = None
        video_id = instance.id
        file_key = (instance.file_key or "").strip()
        hls_prefix = ""
        if video and video.session and video.session.lecture:
            tenant_id = video.session.lecture.tenant_id
            hls_prefix = video_hls_prefix(tenant_id=tenant_id, video_id=video_id)
        # 삭제 전 현재 Job DEAD 처리 (SQS 메시지는 Worker가 job 없을 때 consume)
        if video and video.current_job_id:
            try:
                from apps.support.video.models import VideoTranscodeJob
                cur = VideoTranscodeJob.objects.filter(pk=video.current_job_id).first()
                if cur and cur.state in (VideoTranscodeJob.State.QUEUED, VideoTranscodeJob.State.RETRY_WAIT):
                    cur.state = VideoTranscodeJob.State.DEAD
                    cur.save(update_fields=["state", "updated_at"])
                    logger.info("Video delete: job DEAD video_id=%s job_id=%s", video_id, cur.id)
            except Exception as e:
                logger.warning("Video delete: job DEAD mark failed video_id=%s: %s", video_id, e)
        super().perform_destroy(instance)
        if tenant_id is not None and hls_prefix:
            try:
                VideoSQSQueue().enqueue_delete_r2(
                    tenant_id=tenant_id,
                    video_id=video_id,
                    file_key=file_key,
                    hls_prefix=hls_prefix,
                )
            except Exception as e:
                logger.warning("R2 delete job enqueue failed video_id=%s: %s", video_id, e)

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

        try:
            session = video_repo.get_session_by_id_with_lecture_tenant(session_id)
        except Session.DoesNotExist:
            return Response(
                {"detail": "해당 차시를 찾을 수 없습니다. 페이지를 새로고침한 뒤 다시 시도하세요."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        tenant = session.lecture.tenant
        request_tenant = getattr(request, "tenant", None)
        if request_tenant and tenant.id != request_tenant.id:
            return Response(
                {"detail": "다른 프로그램의 차시에는 업로드할 수 없습니다."},
                status=status.HTTP_403_FORBIDDEN,
            )
        tenant_code = tenant.code
        tenant_id = tenant.id
        order = (
            session.videos.aggregate(max_order=models.Max("order")).get("max_order") or 0
        ) + 1

        ext = filename.split(".")[-1].lower() if "." in filename else "mp4"
        key = video_raw_key(
            tenant_id=tenant_id,
            session_id=session_id,
            unique_id=str(uuid4()),
            ext=ext,
        )

        video = video_repo.create_video(
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
    # public_session — 전체공개영상 업로드/목록용 세션 (테넌트당 1개)
    # ==================================================
    @transaction.atomic
    @action(
        detail=False,
        methods=["get"],
        url_path="public-session",
        url_name="public-session",
    )
    def public_session(self, request):
        """
        테넌트당 "전체공개영상" 전용 Lecture + Session을 get_or_create 하고
        session_id, lecture_id 를 반환합니다.
        이 세션에 올린 영상은 프로그램(테넌트)에 등록된 모든 학생이 시청 가능합니다.
        """
        tenant = getattr(request, "tenant", None)
        if not tenant:
            return Response(
                {"detail": "테넌트를 확인할 수 없습니다. X-Tenant-Code 헤더가 필요합니다. 같은 도메인(예: tchul.com)으로 접속했는지 확인하세요."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        lecture, _ = Lecture.objects.get_or_create(
            tenant=tenant,
            title="전체공개영상",
            defaults={
                "name": "전체공개영상",
                "subject": "공개",
                "description": "프로그램에 등록된 모든 학생이 시청할 수 있는 영상입니다.",
                "is_active": True,
            },
        )
        session, _ = Session.objects.get_or_create(
            lecture=lecture,
            order=1,
            defaults={"title": "전체공개영상", "date": None},
        )
        return Response(
            {"session_id": session.id, "lecture_id": lecture.id},
            status=status.HTTP_200_OK,
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
        try:
            video = self.get_object()
        except Exception as e:
            logger.exception("VIDEO_UPLOAD_COMPLETE_ERROR | get_object | %s", e)
            return Response(
                {"detail": "영상을 찾을 수 없습니다."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not getattr(video, "session", None) or not getattr(video.session, "lecture", None):
            return Response(
                {"detail": "영상이 차시/강의에 연결되어 있지 않아 업로드 완료할 수 없습니다."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not getattr(video.session.lecture, "tenant", None):
            return Response(
                {"detail": "강의의 프로그램(테넌트) 정보가 없어 업로드 완료할 수 없습니다."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            return self._upload_complete_impl(video)
        except Exception as e:
            logger.exception("VIDEO_UPLOAD_COMPLETE_ERROR | video_id=%s | %s", getattr(video, "id", None), e)
            return Response(
                {"detail": "업로드 완료 처리 중 오류가 발생했습니다. 잠시 후 다시 시도하세요."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

    def _upload_complete_impl(self, video):
        """upload_complete 실제 처리 (예외 시 호출부에서 503 반환)."""
        video_id = video.id
        # [TRACE] upload_complete entry
        _tenant_id = getattr(getattr(getattr(video, "session", None), "lecture", None), "tenant_id", None)
        logger.info(
            "VIDEO_UPLOAD_TRACE | upload_complete entry | video_id=%s tenant_id=%s source_path=%s status=%s execution=1_ENTRY",
            video.id,
            _tenant_id,
            video.file_key or "",
            video.status,
        )

        if video.status != Video.Status.PENDING:
            return Response(
                {"detail": f"Invalid status: {video.status}"},
                status=status.HTTP_409_CONFLICT,
            )

        try:
            exists, size = head_object(video.file_key)
        except Exception as e:
            logger.exception("VIDEO_UPLOAD_COMPLETE_ERROR | head_object | video_id=%s | %s", video_id, e)
            return Response(
                {"detail": "저장소 확인 중 오류가 발생했습니다. 잠시 후 다시 시도하세요."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        logger.info(
            "VIDEO_UPLOAD_TRACE | head_object ok | video_id=%s exists=%s size=%s execution=1b_HEAD_OK",
            video_id, exists, size,
        )
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
            logger.exception("VIDEO_UPLOAD_COMPLETE_ERROR | create_presigned_get_url | video_id=%s | %s", video_id, e)
            video.error_reason = f"presigned_get_failed:{str(e)[:200]}"
            video.save(update_fields=["error_reason"])
            return Response(
                {"detail": "presigned_get_failed"},
                status=status.HTTP_409_CONFLICT,
            )
        logger.info("VIDEO_UPLOAD_TRACE | presigned_get ok | video_id=%s execution=1c_PRESIGN_OK", video_id)

        ok, meta, reason = None, {}, ""
        try:
            ok, meta, reason = _validate_source_media_via_ffprobe(src_url)
        except Exception as e:
            logger.exception(
                "VIDEO_UPLOAD_COMPLETE_ERROR | ffprobe raised | video_id=%s | %s | FALLBACK: skip validation, enqueue",
                video_id, e,
            )
            ok, meta, reason = False, {"duration": 0}, "ffprobe_exception_fallback"

        # ffprobe 실패 시(ffmpeg_module_missing, ffprobe_exception 등) 반드시 enqueue
        # duration=None fallback, Worker에서 재검증
        if not ok:
            duration = _safe_int(meta.get("duration"), None)
            video.duration = duration
            video.status = Video.Status.UPLOADED
            video.error_reason = ""
            video.save(update_fields=["status", "duration", "error_reason"])
            logger.info(
                "VIDEO_UPLOAD_TRACE | before enqueue (ffprobe_fail reason=%s duration=%s) | video_id=%s execution=2_BEFORE_ENQUEUE",
                reason, duration, video.id,
            )
            if not VideoSQSQueue().create_job_and_enqueue(video):
                logger.error("VIDEO_UPLOAD_ENQUEUE_FAILED | video_id=%s | reason=%s", video.id, reason)
                return Response(
                    {"detail": "비디오 작업 큐 등록 실패(SQS). API 서버 AWS 설정 및 academy-video-jobs 큐를 확인하세요."},
                    status=status.HTTP_503_SERVICE_UNAVAILABLE,
                )
            return Response(VideoSerializer(video).data)

        min_dur = _safe_int(getattr(settings, "VIDEO_MIN_DURATION_SECONDS", 3), 3)
        duration = _safe_int(meta.get("duration"), None)

        if duration is not None and duration < int(min_dur):
            video.duration = duration
            video.status = Video.Status.UPLOADED
            video.error_reason = ""
            video.save(update_fields=["status", "duration", "error_reason"])
            _tid = getattr(getattr(getattr(video, "session", None), "lecture", None), "tenant_id", None)
            logger.info(
                "VIDEO_UPLOAD_TRACE | before enqueue (duration<min branch) | video_id=%s tenant_id=%s source_path=%s execution=2_BEFORE_ENQUEUE",
                video.id, _tid, video.file_key or "",
            )
            # Job 생성 + SQS enqueue (job_id 포함)
            if not VideoSQSQueue().create_job_and_enqueue(video):
                logger.error(
                    "VIDEO_UPLOAD_ENQUEUE_FAILED | video_id=%s | create_job_and_enqueue returned None (duration<min branch)",
                    video.id,
                )
                return Response(
                    {"detail": "비디오 작업 큐 등록 실패(SQS). API 서버 AWS 설정 및 academy-video-jobs 큐를 확인하세요."},
                    status=status.HTTP_503_SERVICE_UNAVAILABLE,
                )
            return Response(VideoSerializer(video).data)

        video.duration = duration
        video.status = Video.Status.UPLOADED
        video.error_reason = ""
        video.save(update_fields=["status", "duration", "error_reason"])
        _tid = getattr(getattr(getattr(video, "session", None), "lecture", None), "tenant_id", None)
        logger.info(
            "VIDEO_UPLOAD_TRACE | before enqueue (normal branch) | video_id=%s tenant_id=%s source_path=%s execution=2_BEFORE_ENQUEUE",
            video.id, _tid, video.file_key or "",
        )
        # Job 생성 + SQS enqueue (job_id 포함)
        if not VideoSQSQueue().create_job_and_enqueue(video):
            logger.error(
                "VIDEO_UPLOAD_ENQUEUE_FAILED | video_id=%s | create_job_and_enqueue returned None (normal branch)",
                video.id,
            )
            return Response(
                {"detail": "비디오 작업 큐 등록 실패(SQS). API 서버 AWS 설정 및 academy-video-jobs 큐를 확인하세요."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        return Response(VideoSerializer(video).data)

    # ==================================================
    # retry (Job 기반 re-encode)
    # ==================================================
    # 새 VideoTranscodeJob 생성 + Video.current_job 교체 + enqueue(job_id).
    # 기존 RUNNING Job은 cancel_requested 플래그로 협력적 취소.
    @transaction.atomic
    @action(detail=True, methods=["post"], url_path="retry")
    def retry(self, request, pk=None):
        from academy.adapters.db.django.repositories_video import job_set_cancel_requested
        from apps.support.video.models import VideoTranscodeJob

        try:
            video = Video.objects.select_for_update().select_related("session__lecture__tenant").get(
                pk=self.get_object().pk
            )
        except Video.DoesNotExist:
            raise ValidationError("해당 영상을 찾을 수 없습니다.")

        if not getattr(video, "session", None) or not getattr(video.session, "lecture", None):
            raise ValidationError("영상이 차시/강의에 연결되어 있지 않아 재처리할 수 없습니다.")
        if not getattr(video.session.lecture, "tenant", None):
            raise ValidationError("강의의 프로그램(테넌트) 정보가 없어 재처리할 수 없습니다.")

        try:
            # QUEUED/RETRY_WAIT: 최근이면 재시도 불가(이미 백로그). 오래됐으면 메시지 유실로 간주하고 재등록 허용.
            STALE_QUEUED_THRESHOLD = getattr(
                settings, "VIDEO_RETRY_STALE_QUEUED_HOURS", 1
            )  # 1시간 이상 QUEUED/RETRY_WAIT면 재처리 허용
            now = timezone.now()
            stale_cutoff = now - timedelta(hours=STALE_QUEUED_THRESHOLD)

            if video.current_job_id:
                cur = VideoTranscodeJob.objects.filter(pk=video.current_job_id).first()
                if cur and cur.state in (VideoTranscodeJob.State.QUEUED, VideoTranscodeJob.State.RETRY_WAIT):
                    if cur.updated_at >= stale_cutoff:
                        raise ValidationError("Already in backlog (job queued or retry wait)")
                    # 오래된 QUEUED/RETRY_WAIT → 메시지 유실 가능성. 기존 Job DEAD 처리 후 새 Job으로 재등록.
                    cur.state = VideoTranscodeJob.State.DEAD
                    cur.error_message = "Stale; re-enqueued via retry (was QUEUED/RETRY_WAIT too long)"
                    cur.save(update_fields=["state", "error_message", "updated_at"])
                    video.current_job_id = None
                    video.save(update_fields=["current_job_id", "updated_at"])
                elif cur and cur.state == VideoTranscodeJob.State.RUNNING:
                    # RUNNING: cancel_requested 설정 후 새 Job 생성
                    job_set_cancel_requested(cur.id)

            if video.status not in (Video.Status.READY, Video.Status.FAILED):
                if video.status not in (Video.Status.UPLOADED, Video.Status.PROCESSING):
                    raise ValidationError("Cannot retry: status must be READY or FAILED")

            video.status = Video.Status.UPLOADED
            video.save(update_fields=["status", "updated_at"])

            job = VideoSQSQueue().create_job_and_enqueue(video)
            if not job:
                raise ValidationError(
                    "비디오 작업 큐 등록 실패(SQS). API 서버 AWS 설정 및 academy-video-jobs 큐를 확인하세요."
                )

            logger.info(
                "VIDEO_RETRY_ENQUEUED | job_id=%s | video_id=%s | tenant_id=%s",
                job.id, video.id,
                getattr(getattr(getattr(video, "session", None), "lecture", None), "tenant_id", None),
            )
            return Response(
                {"detail": "Video reprocessing queued (SQS)", "job_id": str(job.id)},
                status=status.HTTP_202_ACCEPTED,
            )
        except ValidationError:
            raise
        except Exception as e:
            logger.exception("VIDEO_RETRY_ERROR | video_id=%s | %s", getattr(video, "id", None), e)
            raise ValidationError(
                "재처리 요청 처리 중 오류가 발생했습니다. 잠시 후 다시 시도하거나 관리자에게 문의하세요."
            )

    # ==================================================
    # stats
    # ==================================================
    @action(detail=True, methods=["get"], url_path="stats")
    def stats(self, request, pk=None):
        video = self.get_object()
        lecture = video.session.lecture

        enrollments = video_repo.get_enrollments_for_lecture_active(lecture)

        progresses = {
            p.enrollment_id: p
            for p in video_repo.get_video_progresses_for_video(video)
        }
        perms = {
            p.enrollment_id: p
            for p in video_repo.get_video_access_for_video(video)
        }
        attendance = {
            a.enrollment_id: a.status
            for a in video_repo.get_attendance_for_session(video.session)
        }

        students = []
        for e in enrollments:
            vp = progresses.get(e.id)
            perm = perms.get(e.id)

            # Use SSOT access resolver
            from apps.support.video.services.access_resolver import resolve_access_mode
            access_mode = resolve_access_mode(video=video, enrollment=e)
            
            # Legacy rule for backward compatibility
            rule = perm.rule if perm else "free"
            effective_rule = rule
            if rule == "once" and vp and vp.completed:
                effective_rule = "free"

            lecture = getattr(video.session, "lecture", None) if video.session else None
            students.append(
                {
                    "enrollment": e.id,
                    "student_name": e.student.name,
                    "attendance_status": attendance.get(e.id),
                    "lecture_title": lecture.title if lecture else None,
                    "lecture_color": getattr(lecture, "color", None) if lecture else None,
                    "progress": vp.progress if vp else 0,
                    "completed": vp.completed if vp else False,
                    "rule": rule,  # Legacy field
                    "effective_rule": effective_rule,  # Legacy field
                    "access_mode": access_mode.value,  # New field
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

        enrollments = video_repo.get_enrollments_for_lecture(lecture)
        total = enrollments.count()

        progresses = video_repo.get_video_progresses_for_video(video)
        completed_count = progresses.filter(completed=True).count()

        duration = int(video.duration or 0)

        watched_seconds = 0
        for p in progresses.iterator():
            watched_seconds += int(float(p.progress or 0) * duration)

        completion_rate = (completed_count / total) if total else 0.0

        ev_qs = video_repo.get_playback_events_queryset_for_video(video, since=since)

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

    # ==================================================
    # video folders — 전체공개영상 폴더 관리
    # ==================================================
    @action(
        detail=False,
        methods=["get"],
        url_path="folders",
    )
    def list_folders(self, request):
        """전체공개영상 세션의 폴더 목록 조회."""
        session_id = request.query_params.get("session_id")
        if not session_id:
            return Response(
                {"detail": "session_id required"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            session = video_repo.get_session_by_id_with_lecture_tenant(session_id)
        except Session.DoesNotExist:
            return Response(
                {"detail": "Session not found"},
                status=status.HTTP_404_NOT_FOUND,
            )
        
        folders = VideoFolder.objects.filter(session=session).order_by("order", "name")
        return Response(VideoFolderSerializer(folders, many=True).data)

    @action(
        detail=False,
        methods=["post"],
        url_path="folders",
    )
    def create_folder(self, request):
        """전체공개영상 세션에 폴더 생성."""
        session_id = request.data.get("session_id")
        name = request.data.get("name")
        parent_id = request.data.get("parent_id")  # null이면 루트 폴더
        
        if not session_id or not name:
            return Response(
                {"detail": "session_id and name required"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        
        try:
            session = video_repo.get_session_by_id_with_lecture_tenant(session_id)
        except Session.DoesNotExist:
            return Response(
                {"detail": "Session not found"},
                status=status.HTTP_404_NOT_FOUND,
            )
        
        parent = None
        if parent_id:
            try:
                parent = VideoFolder.objects.get(id=parent_id, session=session)
            except VideoFolder.DoesNotExist:
                return Response(
                    {"detail": "Parent folder not found"},
                    status=status.HTTP_404_NOT_FOUND,
                )
        
        # 같은 이름의 폴더가 이미 있는지 확인
        if VideoFolder.objects.filter(session=session, parent=parent, name=name).exists():
            return Response(
                {"detail": "Folder with this name already exists"},
                status=status.HTTP_409_CONFLICT,
            )
        
        folder = VideoFolder.objects.create(
            session=session,
            parent=parent,
            name=name,
            order=VideoFolder.objects.filter(session=session, parent=parent).count(),
        )
        
        return Response(VideoFolderSerializer(folder).data, status=status.HTTP_201_CREATED)

    @action(
        detail=False,
        methods=["delete"],
        url_path="folders/(?P<folder_id>[^/.]+)",
    )
    def delete_folder(self, request, folder_id=None):
        """전체공개영상 폴더 삭제."""
        try:
            folder = VideoFolder.objects.get(id=folder_id)
        except VideoFolder.DoesNotExist:
            return Response(
                {"detail": "Folder not found"},
                status=status.HTTP_404_NOT_FOUND,
            )
        
        # 하위 폴더나 영상이 있는지 확인
        if folder.children.exists():
            return Response(
                {"detail": "Cannot delete folder with subfolders"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if folder.videos.exists():
            return Response(
                {"detail": "Cannot delete folder with videos"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        
        folder.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)        
        folder.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)
        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)        return Response(status=status.HTTP_204_NO_CONTENT)
        return Response(status=status.HTTP_204_NO_CONTENT)