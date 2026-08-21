from __future__ import annotations

import logging

from django.contrib.auth import get_user_model

from apps.core.models import OpsAuditLog, TenantMembership
from apps.core.services.client_ip import get_client_ip
from apps.domains.students.models import Student

logger = logging.getLogger(__name__)


SCREEN_PRESENTATION = {
    "student.dashboard.home": ("home", "학생 홈을 확인했어요"),
    "student.video.home": ("video", "영상 목록을 열었어요"),
    "student.video.session": ("video", "영상 회차를 열었어요"),
    "student.video.player": ("video", "영상 재생 화면을 열었어요"),
    "student.session.list": ("homework", "수업·숙제 목록을 확인했어요"),
    "student.session.detail": ("homework", "숙제·수업 상세를 열었어요"),
    "student.assignment.submit": ("homework", "숙제 제출 화면을 열었어요"),
    "student.exam.list": ("exam", "시험 목록을 확인했어요"),
    "student.exam.detail": ("exam", "시험 안내를 열었어요"),
    "student.exam.submit": ("exam", "시험 응시 화면을 열었어요"),
    "student.exam.result": ("result", "자기 시험 결과를 확인했어요"),
    "student.grades.home": ("result", "성적 기록을 확인했어요"),
    "student.attendance.home": ("attendance", "출결 기록을 확인했어요"),
    "student.clinic.home": ("clinic", "클리닉 화면을 확인했어요"),
    "student.notice.home": ("notice", "공지·질문 화면을 확인했어요"),
    "student.profile.home": ("profile", "내 정보를 확인했어요"),
    "student.settings.home": ("profile", "설정 화면을 확인했어요"),
    "student.fees.home": ("fee", "수납 내역을 확인했어요"),
    "student.guide.home": ("guide", "사용 안내를 확인했어요"),
}


def _device_class(request, supplied: str = "") -> str:
    if supplied in {"mobile", "tablet", "desktop"}:
        return supplied
    user_agent = str(request.META.get("HTTP_USER_AGENT") or "").lower()
    if any(token in user_agent for token in ("iphone", "android", "mobile")):
        return "mobile"
    if any(token in user_agent for token in ("ipad", "tablet")):
        return "tablet"
    return "desktop"


def _request_context(request) -> tuple[str, str]:
    return (
        get_client_ip(request)[:64],
        str(request.META.get("HTTP_USER_AGENT") or "")[:255],
    )


def _create_activity(
    *,
    request,
    student: Student,
    actor_user,
    actor_mode: str,
    action: str,
    summary: str,
    category: str,
    device_class: str,
    screen_id: str,
) -> None:
    ip, user_agent = _request_context(request)
    OpsAuditLog.objects.create(
        actor_user=actor_user,
        actor_username=str(getattr(actor_user, "username", "") or "")[:150],
        action=action,
        summary=summary,
        target_tenant=student.tenant,
        target_user=student.user,
        payload={
            "student_id": student.id,
            "actor_mode": actor_mode,
            "category": category,
            "device_class": device_class,
            "screen_id": screen_id,
        },
        ip=ip,
        user_agent=user_agent,
    )


def record_student_login(*, request, tenant, user) -> None:
    """Record only a real student credential login, never a support session."""

    try:
        student = Student.objects.filter(
            tenant=tenant,
            user=user,
            deleted_at__isnull=True,
        ).first()
        if student is None:
            return
        if not TenantMembership.objects.filter(
            tenant=tenant,
            user=user,
            role="student",
            is_active=True,
        ).exists():
            return

        _create_activity(
            request=request,
            student=student,
            actor_user=user,
            actor_mode="student",
            action="student_activity.login",
            summary="학생 앱에 로그인했어요",
            category="login",
            device_class=_device_class(request),
            screen_id="auth.login.student",
        )
    except Exception:
        # A telemetry write must not make a valid student credential unusable.
        logger.exception(
            "student login activity persistence failed: tenant_id=%s user_id=%s",
            getattr(tenant, "id", None),
            getattr(user, "id", None),
        )


def record_student_screen_view(
    *,
    request,
    screen_id: str,
    device_class: str,
) -> bool:
    presentation = SCREEN_PRESENTATION.get(str(screen_id or "").strip())
    if presentation is None:
        return False

    tenant = getattr(request, "tenant", None)
    if tenant is None:
        return False
    student = Student.objects.select_related("user", "tenant").filter(
        tenant=tenant,
        user=request.user,
        deleted_at__isnull=True,
    ).first()
    if student is None:
        return False
    if not TenantMembership.objects.filter(
        tenant=tenant,
        user=request.user,
        role="student",
        is_active=True,
    ).exists():
        return False

    auth = getattr(request, "auth", None)
    getter = getattr(auth, "get", None)
    support_preview = bool(getter("support_preview")) if callable(getter) else False
    impersonated_by = getter("impersonated_by") if callable(getter) else None
    actor_user = request.user
    actor_mode = "student"
    if support_preview and impersonated_by:
        actor_user = get_user_model().objects.filter(
            pk=impersonated_by,
            tenant=tenant,
            is_active=True,
        ).first()
        if actor_user is None or not TenantMembership.objects.filter(
            tenant=tenant,
            user=actor_user,
            role__in=("owner", "admin", "teacher", "staff"),
            is_active=True,
        ).exists():
            return False
        actor_mode = "support"

    category, summary = presentation
    _create_activity(
        request=request,
        student=student,
        actor_user=actor_user,
        actor_mode=actor_mode,
        action="student_activity.screen_view",
        summary=summary,
        category=category,
        device_class=_device_class(request, device_class),
        screen_id=screen_id,
    )
    return True
