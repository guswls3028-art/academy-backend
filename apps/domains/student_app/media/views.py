from typing import Any, Dict, Optional, Tuple

from django.http import Http404
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework.exceptions import PermissionDenied
from rest_framework import status

from apps.domains.student_app.permissions import IsStudentOrParent, get_request_student
from .serializers import (
    StudentVideoListItemSerializer,
    StudentVideoPlaybackSerializer,
)


# ======================================================
# 내부 유틸 (운영 안정성 우선)
# ======================================================

def _import_media_models():
    try:
        from apps.support.video.models import Video, VideoAccess
    except Exception as e:
        raise RuntimeError(
            "[CRITICAL] apps.support.video.models.Video import 실패"
        ) from e
    return Video, VideoAccess


def _get_student_enrollment_id(request) -> Optional[int]:
    q = request.query_params.get("enrollment")
    if q:
        try:
            return int(q)
        except Exception:
            return None

    sp = get_request_student(request)
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


def _get_enrollment_for_student(request, enrollment_id: Optional[int], lecture_id: Optional[int] = None):
    """
    요청한 학생 소유의 수강정보만 허용 (IDOR 방지).
    lecture_id가 주어지면 해당 강의의 수강인지 검증.
    Returns: (enrollment_obj or None, error Response or None)
    """
    from apps.domains.enrollment.models import Enrollment

    if not enrollment_id:
        return None, None
    student = get_request_student(request)
    if not student:
        return None, Response(
            {"detail": "학생 정보를 확인할 수 없습니다."},
            status=status.HTTP_403_FORBIDDEN,
        )
    enrollment = Enrollment.objects.filter(id=enrollment_id, student=student, status="ACTIVE").first()
    if not enrollment:
        return None, Response(
            {"detail": "해당 수강 정보에 접근할 수 없습니다."},
            status=status.HTTP_403_FORBIDDEN,
        )
    # lecture_id가 주어졌을 때 다른 강의 수강이면 None 반환 (403 아님: 세션 목록에서 무시하고 진행률 0으로 표시)
    if lecture_id is not None and enrollment.lecture_id != lecture_id:
        return None, None
    return enrollment, None


def _pick_urls(video, request=None) -> Tuple[Optional[str], Optional[str]]:
    """
    비디오 재생 URL 생성
    - hls_url: CDN 기반 HLS URL (VideoPlaybackMixin._public_play_url 로직 사용)
    - mp4_url: MP4 URL (현재는 미지원)
    """
    from django.conf import settings
    from django.utils import timezone
    from apps.support.video.views.playback_mixin import VideoPlaybackMixin
    
    # 비디오 상태 확인
    if not hasattr(video, "status") or video.status != video.Status.READY:
        import logging
        logger = logging.getLogger(__name__)
        logger.warning(f"[_pick_urls] Video {video.id} is not READY (status: {getattr(video, 'status', 'UNKNOWN')})")
        return None, None
    
    # VideoPlaybackMixin의 _public_play_url 로직 사용
    mixin = VideoPlaybackMixin()
    
    # expires_at은 24시간 후로 설정 (학생 앱은 세션 관리가 단순하므로 충분히 긴 시간)
    expires_at = int(timezone.now().timestamp()) + (24 * 3600)
    
    # user_id는 request에서 가져오거나 기본값 사용
    user_id = getattr(request.user, "id", 0) if request and hasattr(request, "user") and request.user.is_authenticated else 0
    
    try:
        # 비디오 정보 로깅
        import logging
        logger = logging.getLogger(__name__)
        hls_path = getattr(video, "hls_path", None)
        file_key = getattr(video, "file_key", None)
        tenant_id = None
        try:
            if hasattr(video, "session") and video.session:
                if hasattr(video.session, "lecture") and video.session.lecture:
                    tenant_id = getattr(video.session.lecture, "tenant_id", None)
        except Exception:
            pass
        
        logger.info(
            f"[_pick_urls] Generating URL for video {video.id}: "
            f"hls_path={hls_path}, file_key={file_key}, tenant_id={tenant_id}, "
            f"expires_at={expires_at}, user_id={user_id}"
        )
        
        hls_url = mixin._public_play_url(
            video=video,
            expires_at=expires_at,
            user_id=user_id,
        )
        
        logger.info(f"[_pick_urls] Generated URL for video {video.id}: {hls_url[:200] if hls_url else None}...")
        
        # URL이 생성되었는지 확인
        if not hls_url:
            logger.warning(f"[_pick_urls] _public_play_url returned None for video {video.id}")
            return None, None
            
    except Exception as e:
        # 에러 발생 시 로그만 남기고 None 반환
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f"[_pick_urls] Failed to generate HLS URL for video {video.id}: {e}", exc_info=True)
        hls_url = None
    
    # MP4 URL은 현재 미지원
    mp4_url = None
    
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


class StudentPublicSessionView(APIView):
    """
    GET /student/video/public-session/
    테넌트별 전체공개영상 세션 ID 반환. 같은 테넌트 학생만 호출 가능.
    """

    permission_classes = [IsAuthenticated, IsStudentOrParent]

    def get(self, request):
        from apps.domains.lectures.models import Lecture, Session

        tenant = getattr(request, "tenant", None)
        student = get_request_student(request)
        if not tenant or not student:
            return Response(
                {"detail": "tenant or student required"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        # 전체공개: 수강등록 없이 해당 테넌트 소속 학생이면 허용 (1테넌트=1프로그램)
        if getattr(student, "tenant_id", None) != getattr(tenant, "id", None):
            return Response(
                {"detail": "전체공개 영상은 해당 학원 소속 학생만 이용할 수 있습니다."},
                status=status.HTTP_403_FORBIDDEN,
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


class StudentVideoMeView(APIView):
    """
    GET /student/video/me/
    영상 탭용: 전체공개 세션 정보 + 수강 중인 강의별 차시 목록.
    """

    permission_classes = [IsAuthenticated, IsStudentOrParent]

    def get(self, request):
        from apps.domains.lectures.models import Lecture, Session
        from apps.domains.enrollment.models import Enrollment

        tenant = getattr(request, "tenant", None)
        student = get_request_student(request)
        if not tenant or not student:
            return Response(
                {"detail": "tenant or student required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        enrollments = (
            Enrollment.objects.filter(student=student, tenant=tenant, status="ACTIVE")
            .select_related("lecture")
            .order_by("lecture__title")
        )
        enrollment_by_lecture = {e.lecture_id: e.id for e in enrollments}
        lecture_ids = list(enrollment_by_lecture.keys())
        lectures_qs = (
            Lecture.objects.filter(id__in=lecture_ids, tenant=tenant)
            .prefetch_related("sessions")
            .order_by("title")
        )
        lectures_data = []
        for lec in lectures_qs:
            sessions_data = [
                {
                    "id": s.id,
                    "title": s.title or f"{s.order}차시",
                    "order": s.order,
                    "date": s.date.isoformat() if s.date else None,
                }
                for s in sorted(lec.sessions.all(), key=lambda x: (x.order, x.id))
            ]
            lectures_data.append({
                "id": lec.id,
                "title": lec.title or lec.name or "강의",
                "sessions": sessions_data,
                "enrollment_id": enrollment_by_lecture.get(lec.id),
            })

        # 전체공개영상: 내용물이 있던 없던 항상 제공 (학생이면 다 볼 수 있음)
        public_lecture = Lecture.objects.filter(
            tenant=tenant, title="전체공개영상"
        ).first()
        public_data = None
        if public_lecture:
            public_session = public_lecture.sessions.filter(order=1).first()
            if public_session:
                public_data = {
                    "session_id": public_session.id,
                    "lecture_id": public_lecture.id,
                }
        # 전체공개영상이 없어도 항상 public 필드 제공 (null로)
        # 프론트엔드에서 전체공개영상 선택지를 항상 표시할 수 있도록

        return Response({
            "public": public_data,  # null이어도 항상 필드 제공
            "lectures": lectures_data,
        }, status=status.HTTP_200_OK)


def _students_for_request(request):
    """요청자에 연결된 학생들 (1명 또는 학부모의 모든 자녀). 권한 검사용."""
    student = get_request_student(request)
    if student:
        return [student]
    from apps.domains.parents.models import Parent
    parent = getattr(request.user, "parent_profile", None)
    if parent:
        return list(parent.students.filter(deleted_at__isnull=True))
    return []


def _student_can_access_session(request, session) -> bool:
    """세션 접근: 전체공개영상 = 테넌트 내 학생이면 OK. 그 외 = 해당 강의 수강생만."""
    from apps.domains.enrollment.models import Enrollment

    lecture = getattr(session, "lecture", None)
    if not lecture:
        return False
    tenant = getattr(lecture, "tenant", None) or getattr(request, "tenant", None)
    if not tenant:
        return False
    tenant_id = getattr(tenant, "id", None)

    # 전체공개영상: 테넌트 내 학생이면 끝 (조건 추가 없음)
    if getattr(lecture, "title", None) == "전체공개영상":
        students = _students_for_request(request)
        return bool(students and any(getattr(s, "tenant_id", None) == tenant_id for s in students))

    # 세션영상: 해당 세션 강의 수강생만
    students = _students_for_request(request)
    if not students:
        return False
    for student in students:
        if Enrollment.objects.filter(
            student=student, lecture=lecture, tenant=tenant, status="ACTIVE"
        ).exists():
            return True
    return False


class StudentSessionVideoListView(APIView):
    """
    GET /student/video/sessions/{session_id}/videos/
    """

    permission_classes = [IsAuthenticated, IsStudentOrParent]

    def get(self, request, session_id: int):
        from apps.domains.lectures.models import Session as SessionModel

        Video, VideoPermission = _import_media_models()
        enrollment_id = _get_student_enrollment_id(request)

        try:
            session = SessionModel.objects.select_related("lecture__tenant").get(id=session_id)
        except SessionModel.DoesNotExist:
            raise Http404

        lecture = getattr(session, "lecture", None)
        is_public = lecture and getattr(lecture, "title", None) == "전체공개영상"

        # 전체공개영상: 테넌트 내 학생이면 enrollment 없이 허용
        if is_public and _student_can_access_session(request, session):
            enrollment_obj = None
        else:
            enrollment_obj = None
            if enrollment_id:
                enrollment_obj, err = _get_enrollment_for_student(
                    request, enrollment_id, lecture_id=getattr(lecture, "id", None)
                )
                if err:
                    return err
            if enrollment_obj is None and not _student_can_access_session(request, session):
                detail = (
                    "전체공개 영상은 해당 학원 소속 학생만 이용할 수 있습니다."
                    if is_public
                    else "이 차시의 영상을 볼 수 있는 권한이 없습니다."
                )
                raise PermissionDenied(detail)

        videos = Video.objects.filter(session_id=session_id).order_by("order", "id")

        # 진행률 일괄 조회: 요청 학생 소유의 수강정보만 사용 (IDOR 방지)
        from academy.adapters.db.django import repositories_video as video_repo

        progress_map = {}
        if enrollment_obj:
            # 세션 내 모든 영상의 진행률을 일괄 조회 (최적화)
            video_ids = list(videos.values_list("id", flat=True))
            if video_ids:
                progresses = video_repo.video_progress_filter_video_enrollment_ids(
                    video=None,
                    enrollment_ids=[enrollment_obj.id],
                ).filter(video_id__in=video_ids)
                progress_map = {p.video_id: p for p in progresses}

        items = []
        for v in videos:
            perm_obj = None
            if VideoPermission and enrollment_obj:
                perm_obj = (
                    VideoPermission.objects
                    .filter(video_id=v.id, enrollment_id=enrollment_obj.id)
                    .first()
                )

            thumb = getattr(v, "thumbnail_url", None) or getattr(v, "thumbnail", None)

            # Use SSOT access resolver
            from apps.support.video.services.access_resolver import resolve_access_mode
            
            access_mode_value = None
            if enrollment_obj:
                access_mode_value = resolve_access_mode(video=v, enrollment=enrollment_obj).value
            
            # 진행률 계산 (0-100)
            progress_obj = progress_map.get(v.id)
            progress_percent = 0
            if progress_obj:
                progress_percent = round(float(progress_obj.progress or 0) * 100, 1)
            
            items.append({
                "id": int(v.id),
                "session_id": int(v.session_id),
                "title": str(v.title),
                "status": str(getattr(v, "status", "READY")),
                "thumbnail_url": thumb,
                "duration": getattr(v, "duration", None),
                "progress": progress_percent,  # 0-100
                "completed": bool(progress_obj and progress_obj.completed) if progress_obj else False,
                **_policy_from_video(v),
                "effective_rule": _effective_rule(perm_obj),  # Legacy field
                "access_mode": access_mode_value,  # New field
            })

        return Response({
            "items": StudentVideoListItemSerializer(items, many=True).data
        })


class StudentVideoPlaybackView(APIView):
    """
    GET /student/video/videos/{video_id}/playback/
    """

    permission_classes = [IsAuthenticated, IsStudentOrParent]

    def get(self, request, video_id: int):
        Video, VideoPermission = _import_media_models()
        enrollment_id = _get_student_enrollment_id(request)
        enrollment_obj = None  # 일반 영상일 때 검증 후 설정, 전체공개는 None

        try:
            video = Video.objects.select_related("session__lecture__tenant").get(id=video_id)
        except Video.DoesNotExist:
            raise Http404

        # 전체공개영상 세션인지 확인
        is_public_session = False
        if video.session and video.session.lecture:
            is_public_session = getattr(video.session.lecture, "title", None) == "전체공개영상"

        if is_public_session:
            # 전체공개영상: 수강등록 없이, 해당 테넌트 소속 학생만 시청 가능 (1테넌트=1프로그램)
            student = get_request_student(request)
            lecture_tenant_id = getattr(video.session.lecture, "tenant_id", None)
            if not student or getattr(student, "tenant_id", None) != lecture_tenant_id:
                raise PermissionDenied("전체공개 영상은 해당 학원 소속 학생만 시청할 수 있습니다.")
        elif not enrollment_id:
            # 일반 영상: 수강 정보 필요
            raise PermissionDenied("이 영상을 시청하려면 수강 정보가 필요합니다.")
        else:
            # 일반 영상: enrollment가 요청 학생 소유이며 이 영상 강의의 수강인지 검증 (IDOR 방지)
            lecture_id = getattr(video.session.lecture, "id", None) if video.session and video.session.lecture else None
            enrollment_obj, err = _get_enrollment_for_student(request, enrollment_id, lecture_id=lecture_id)
            if err:
                return err
            if not enrollment_obj:
                raise PermissionDenied("해당 수강 정보로는 이 영상을 시청할 수 없습니다.")

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

        # Use SSOT access resolver (enrollment_obj는 위에서 검증된 객체만 사용)
        from apps.support.video.services.access_resolver import resolve_access_mode

        access_mode_value = None
        if enrollment_obj:
            access_mode_value = resolve_access_mode(video=video, enrollment=enrollment_obj).value

        # 비디오 상태 확인 및 로깅
        import logging
        logger = logging.getLogger(__name__)
        
        video_status = getattr(video, "status", None)
        hls_path = getattr(video, "hls_path", None)
        file_key = getattr(video, "file_key", None)
        
        logger.info(
            f"[StudentVideoPlaybackView] Video {video_id} playback request: "
            f"status={video_status}, hls_path={hls_path}, file_key={file_key}, enrollment_id={enrollment_id}"
        )
        
        # 비디오가 READY 상태가 아니면 에러 반환
        if video_status != video.Status.READY:
            logger.warning(
                f"[StudentVideoPlaybackView] Video {video_id} is not READY: status={video_status}"
            )
            return Response(
                {
                    "detail": f"비디오가 아직 준비되지 않았습니다. (상태: {video_status})",
                    "video_status": str(video_status),
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        
        hls_url, mp4_url = _pick_urls(video, request)
        
        # 재생 URL이 없으면 에러 반환
        if not hls_url and not mp4_url:
            logger.error(
                f"[StudentVideoPlaybackView] Failed to generate playback URL for video {video_id}: "
                f"hls_path={hls_path}, file_key={file_key}"
            )
            return Response(
                {
                    "detail": "비디오 재생 URL을 생성할 수 없습니다. 비디오 파일이 처리 중이거나 업로드되지 않았을 수 있습니다.",
                    "video_status": str(video_status),
                    "hls_path": hls_path,
                    "has_file_key": bool(file_key),
                },
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        
        thumb = getattr(video, "thumbnail_url", None) or getattr(video, "thumbnail", None)
        
        # thumbnail_url이 없으면 thumbnail 필드에서 URL 생성
        if not thumb and hasattr(video, "thumbnail") and video.thumbnail:
            thumb = request.build_absolute_uri(video.thumbnail.url) if request else None

        # play_url 생성 (hls_url 우선, 없으면 mp4_url)
        play_url = hls_url or mp4_url
        
        logger.info(
            f"[StudentVideoPlaybackView] Generated playback URL for video {video_id}: "
            f"play_url={play_url[:100] if play_url else None}..."
        )

        payload = {
            "video": {
                "id": int(video.id),
                "session_id": int(video.session_id),
                "title": str(video.title),
                "status": str(getattr(video, "status", "READY")),
                "thumbnail_url": thumb,
                "duration": getattr(video, "duration", None),
                **_policy_from_video(video),
                "effective_rule": rule,  # Legacy field
                "access_mode": access_mode_value,  # New field
            },
            "hls_url": hls_url,
            "mp4_url": mp4_url,
            "play_url": play_url,  # ✅ 재생 URL 추가
            "policy": {
                **_policy_from_video(video),
                "effective_rule": rule,  # Legacy field
                "access_mode": access_mode_value,  # New field
            },
        }

        return Response(
            StudentVideoPlaybackSerializer(payload).data,
            status=status.HTTP_200_OK,
        )


class StudentVideoProgressView(APIView):
    """
    POST /student/video/videos/{video_id}/progress/
    비디오 진행률 업데이트 (수강 완료, 다시보기 등)
    """

    permission_classes = [IsAuthenticated, IsStudentOrParent]

    def post(self, request, video_id: int):
        Video, VideoPermission = _import_media_models()
        from apps.support.video.models import VideoProgress
        from apps.domains.enrollment.models import Enrollment

        enrollment_id = _get_student_enrollment_id(request)

        try:
            video = Video.objects.select_related("session__lecture").get(id=video_id)
        except Video.DoesNotExist:
            raise Http404

        # 전체공개영상: 수강등록 없이 시청 가능. VideoProgress는 (video, enrollment) 필수라 DB 저장 불가.
        # 동일 응답 형태로 200 반환해 프론트 스펙 유지 (DB 미저장)
        is_public_lecture = (
            video.session
            and video.session.lecture
            and getattr(video.session.lecture, "title", None) == "전체공개영상"
        )
        if is_public_lecture:
            progress_value = request.data.get("progress", None)
            completed = request.data.get("completed", False)
            try:
                p = float(progress_value) if progress_value is not None else 0.0
                if p > 1:
                    p = p / 100.0
                p = max(0.0, min(1.0, p))
            except (TypeError, ValueError):
                p = 0.0
            return Response({
                "id": 0,
                "video_id": video.id,
                "enrollment_id": 0,
                "progress": p,
                "progress_percent": round(p * 100, 1),
                "completed": bool(completed),
                "last_position": int(request.data.get("last_position") or 0),
            }, status=status.HTTP_200_OK)

        if not enrollment_id:
            return Response(
                {"detail": "enrollment_id가 필요합니다."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        lecture_id = getattr(video.session, "lecture_id", None) if video.session else None
        enrollment, err = _get_enrollment_for_student(request, enrollment_id, lecture_id=lecture_id)
        if err:
            return err
        if not enrollment:
            return Response(
                {"detail": "해당 수강 정보로는 진행률을 저장할 수 없습니다."},
                status=status.HTTP_403_FORBIDDEN,
            )

        # 진행률 업데이트 또는 생성
        progress_value = request.data.get("progress", None)  # 0-1 또는 0-100
        completed = request.data.get("completed", None)  # boolean
        last_position = request.data.get("last_position", None)  # seconds

        # progress를 0-1로 정규화
        if progress_value is not None:
            if progress_value > 1:
                progress_value = progress_value / 100.0
            progress_value = max(0.0, min(1.0, float(progress_value)))

        progress_obj, created = VideoProgress.objects.get_or_create(
            video=video,
            enrollment=enrollment,
            defaults={
                "progress": progress_value if progress_value is not None else 0.0,
                "completed": completed if completed is not None else False,
                "last_position": last_position if last_position is not None else 0,
            },
        )

        if not created:
            # 기존 레코드 업데이트
            if progress_value is not None:
                progress_obj.progress = progress_value
            if completed is not None:
                progress_obj.completed = completed
            if last_position is not None:
                progress_obj.last_position = last_position
            progress_obj.save()

        return Response({
            "id": progress_obj.id,
            "video_id": video.id,
            "enrollment_id": enrollment.id,
            "progress": progress_obj.progress,
            "progress_percent": round(float(progress_obj.progress) * 100, 1),
            "completed": progress_obj.completed,
            "last_position": progress_obj.last_position,
        }, status=status.HTTP_200_OK)
