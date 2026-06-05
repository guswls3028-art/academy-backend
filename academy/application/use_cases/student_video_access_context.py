from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from rest_framework import status

from apps.domains.student_app.permissions import get_request_student
from apps.domains.students.selectors import active_students_for_parent, student_for_tenant_user
from apps.domains.video.models import AccessMode
from apps.domains.video.policy import (
    is_video_progress_complete,
    normalize_video_progress,
)
from apps.domains.video.services.access_resolver import resolve_access_mode


class StudentVideoAccessError(Exception):
    def __init__(self, detail: str, status_code: int = status.HTTP_403_FORBIDDEN):
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code


@dataclass(frozen=True)
class StudentSessionVideoContext:
    enrollment: object | None
    is_public_session: bool


@dataclass(frozen=True)
class StudentVideoAccessContext:
    enrollment: object | None
    access_mode: AccessMode | None
    is_public_video: bool

    @property
    def access_mode_value(self) -> str | None:
        return self.access_mode.value if self.access_mode else None

    @property
    def is_blocked(self) -> bool:
        return self.access_mode == AccessMode.BLOCKED


def get_students_for_request(request):
    tenant = getattr(request, "tenant", None)
    if not tenant:
        return []

    student = student_for_tenant_user(tenant, request.user, deleted="active")
    if student:
        return [student]

    parent = getattr(request.user, "parent_profile", None)
    if parent:
        active_students = active_students_for_parent(tenant, parent)
        header_id = request.META.get("HTTP_X_STUDENT_ID")
        if header_id:
            try:
                selected = active_students.filter(id=int(header_id)).first()
            except (TypeError, ValueError):
                selected = None
            return [selected] if selected else []
        return list(active_students)
    return []


def get_enrollment_for_student(request, enrollment_id: Optional[int], lecture_id: Optional[int] = None):
    from apps.domains.enrollment.models import Enrollment

    if not enrollment_id:
        return None
    students = get_students_for_request(request)
    if not students:
        raise StudentVideoAccessError("학생 정보를 확인할 수 없습니다.")
    tenant = getattr(request, "tenant", None)
    if not tenant:
        raise StudentVideoAccessError("tenant required")

    enrollment = (
        Enrollment.objects
        .filter(id=enrollment_id, student__in=students, status="ACTIVE", tenant=tenant)
        .first()
    )
    if not enrollment:
        raise StudentVideoAccessError("해당 수강 정보에 접근할 수 없습니다.")
    if lecture_id is not None and enrollment.lecture_id != lecture_id:
        raise StudentVideoAccessError(
            "수강 정보가 해당 강의와 일치하지 않습니다.",
            status.HTTP_400_BAD_REQUEST,
        )
    return enrollment


def find_active_enrollment_for_lecture(request, lecture_id: Optional[int], explicit_enrollment_id: Optional[int] = None):
    if explicit_enrollment_id:
        return get_enrollment_for_student(request, explicit_enrollment_id, lecture_id=lecture_id)

    students = get_students_for_request(request)
    tenant = getattr(request, "tenant", None)
    if not students or not tenant or not lecture_id:
        return None

    from apps.domains.enrollment.models import Enrollment

    return (
        Enrollment.objects
        .filter(student__in=students, lecture_id=lecture_id, status="ACTIVE", tenant=tenant)
        .order_by("-id")
        .first()
    )


def find_active_enrollment_for_video(request, video, explicit_enrollment_id: Optional[int] = None):
    session = getattr(video, "session", None)
    lecture_id = getattr(session, "lecture_id", None) if session else None
    return find_active_enrollment_for_lecture(
        request,
        lecture_id,
        explicit_enrollment_id=explicit_enrollment_id,
    )


def ensure_public_lecture_enrollment(request, lecture):
    from apps.domains.enrollment.models import Enrollment

    tenant = getattr(request, "tenant", None)
    student = get_request_student(request)
    if not tenant or not student:
        raise StudentVideoAccessError("학생 정보를 확인할 수 없습니다.")
    if getattr(lecture, "tenant_id", None) != tenant.id or getattr(student, "tenant_id", None) != tenant.id:
        raise StudentVideoAccessError("공개 영상은 해당 학원 소속 학생만 이용할 수 있습니다.")

    enrollment, _ = Enrollment.objects.get_or_create(
        tenant=tenant,
        student=student,
        lecture=lecture,
        defaults={"status": "ACTIVE"},
    )
    if enrollment.status != "ACTIVE":
        enrollment.status = "ACTIVE"
        enrollment.save(update_fields=["status", "updated_at"])
    return enrollment


def ensure_public_video_enrollment(request, video):
    session = getattr(video, "session", None)
    lecture = getattr(session, "lecture", None) if session else None
    if not lecture:
        raise StudentVideoAccessError("공개 영상의 강의 정보를 확인할 수 없습니다.")
    return ensure_public_lecture_enrollment(request, lecture)


def student_can_access_session(request, session) -> bool:
    from apps.domains.enrollment.models import Enrollment

    lecture = getattr(session, "lecture", None)
    if not lecture:
        return False
    tenant = getattr(lecture, "tenant", None)
    if not tenant:
        return False
    tenant_id = getattr(tenant, "id", None)
    students = get_students_for_request(request)
    if not students:
        return False

    if getattr(lecture, "is_system", False):
        return any(getattr(s, "tenant_id", None) == tenant_id for s in students)

    for student in students:
        if Enrollment.objects.filter(
            student=student,
            lecture=lecture,
            tenant=tenant,
            status="ACTIVE",
        ).exists():
            return True
    return False


def _video_tenant_id(video) -> int | None:
    video_tenant_id = getattr(video, "tenant_id", None)
    if video_tenant_id is not None:
        return video_tenant_id
    session = getattr(video, "session", None)
    lecture = getattr(session, "lecture", None) if session else None
    return getattr(lecture, "tenant_id", None) if lecture else None


def is_public_video(video) -> bool:
    from apps.domains.video.models import Video

    return getattr(video, "visibility", Video.Visibility.ENROLLED) == Video.Visibility.PUBLIC


def student_can_access_video(request, video) -> bool:
    tenant = getattr(request, "tenant", None)
    if not tenant:
        return False
    if _video_tenant_id(video) != tenant.id:
        return False

    if is_public_video(video):
        return any(getattr(student, "tenant_id", None) == tenant.id for student in get_students_for_request(request))

    session = getattr(video, "session", None)
    return bool(session and student_can_access_session(request, session))


def resolve_student_session_video_context(
    request,
    session,
    *,
    explicit_enrollment_id: Optional[int] = None,
) -> StudentSessionVideoContext:
    lecture = getattr(session, "lecture", None)
    is_public = bool(lecture and getattr(lecture, "is_system", False))

    if is_public and student_can_access_session(request, session):
        return StudentSessionVideoContext(
            enrollment=ensure_public_lecture_enrollment(request, lecture),
            is_public_session=True,
        )

    lecture_id = getattr(lecture, "id", None)
    enrollment = find_active_enrollment_for_lecture(
        request,
        lecture_id,
        explicit_enrollment_id=explicit_enrollment_id,
    )
    if enrollment is None and not student_can_access_session(request, session):
        detail = (
            "공개 영상은 해당 학원 소속 학생만 이용할 수 있습니다."
            if is_public
            else "이 차시의 영상을 볼 수 있는 권한이 없습니다."
        )
        raise StudentVideoAccessError(detail)

    return StudentSessionVideoContext(
        enrollment=enrollment,
        is_public_session=is_public,
    )


def resolve_student_video_access_context(
    request,
    video,
    *,
    explicit_enrollment_id: Optional[int] = None,
) -> StudentVideoAccessContext:
    if is_public_video(video):
        if not student_can_access_video(request, video):
            raise StudentVideoAccessError("공개 영상은 해당 학원 소속 학생만 이용할 수 있습니다.")
        return StudentVideoAccessContext(
            enrollment=ensure_public_video_enrollment(request, video),
            access_mode=None,
            is_public_video=True,
        )

    enrollment = find_active_enrollment_for_video(
        request,
        video,
        explicit_enrollment_id=explicit_enrollment_id,
    )
    if not enrollment:
        raise StudentVideoAccessError("이 영상을 시청하려면 해당 강의에 수강 등록이 필요합니다.")

    return StudentVideoAccessContext(
        enrollment=enrollment,
        access_mode=resolve_access_mode(video=video, enrollment=enrollment),
        is_public_video=False,
    )


def ensure_student_video_watch_allowed(context: StudentVideoAccessContext) -> None:
    if context.is_blocked:
        raise StudentVideoAccessError("이 영상은 시청이 제한되었습니다.")
