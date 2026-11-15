from django.db.models import Exists, F, OuterRef, Q

from academy.adapters.db.django import repositories_video as video_repo
from academy.application.use_cases.student_video_access_context import lecture_allows_student_learning
from apps.core.models import TenantMembership
from apps.domains.enrollment.models import Enrollment
from apps.domains.students.models import Student
from apps.domains.video.models import AccessMode, DirectVideoEntitlement, InactiveVideoEntitlement, Video, VideoAccess


def active_review_students(video):
    memberships = TenantMembership.objects.filter(
        tenant_id=video.tenant_id, user_id=OuterRef("user_id"), role="student", is_active=True,
    )
    return Student.objects.filter(
        tenant_id=video.tenant_id, deleted_at__isnull=True, user__is_active=True,
    ).filter(Exists(memberships))


def review_report_recipients(video, now):
    students = active_review_students(video)
    session = video.session
    if video.status != Video.Status.READY or (session and not lecture_allows_student_learning(session.lecture)):
        return students.none()
    if video.visibility == Video.Visibility.PUBLIC:
        return students
    if not session:
        return students.none()

    enrollments = Enrollment.objects.filter(tenant_id=video.tenant_id, lecture_id=session.lecture_id)
    blocked = VideoAccess.objects.filter(video=video).filter(
        Q(access_mode=AccessMode.BLOCKED) | Q(rule="blocked"),
    ).values("enrollment_id")
    memberships = video_repo.video_session_memberships(
        tenant_id=video.tenant_id, enrollment_ids=enrollments.values("pk"),
    ).filter(session=session).exclude(enrollment_id__in=blocked)
    recipients = Q(pk__in=memberships.filter(enrollment__status="ACTIVE").values("enrollment__student_id"))
    if video.source_type != Video.SourceType.YOUTUBE and session.lecture.is_active:
        inactive = InactiveVideoEntitlement.objects.filter(
            tenant_id=video.tenant_id, video=video, revoked_at__isnull=True,
            source=InactiveVideoEntitlement.Source.STAFF_AUTHORIZATION,
            student_id=F("enrollment__student_id"),
            enrollment_id__in=memberships.filter(enrollment__status="INACTIVE").values("enrollment_id"),
        ).filter(Q(expires_at__isnull=True) | Q(expires_at__gt=now)).values("student_id")
        direct = DirectVideoEntitlement.objects.filter(
            tenant_id=video.tenant_id, video=video, revoked_at__isnull=True,
            source=DirectVideoEntitlement.Source.STAFF_AUTHORIZATION,
        ).exclude(student_id__in=enrollments.values("student_id")).values("student_id")
        recipients |= Q(pk__in=inactive) | Q(pk__in=direct)
    return students.filter(recipients)
