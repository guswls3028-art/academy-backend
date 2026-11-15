"""Read-only playback grants for verified staff support sessions."""

from django.conf import settings
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied

from apps.domains.video.drm import create_playback_token


def support_preview_binding(request):
    session = getattr(request, "student_support_session", None)
    if session is None:
        return {}
    return {
        "support_session_id": str(session.pk),
        "operator_id": session.operator_id,
        "student_id": session.student_id,
        "tenant_id": session.tenant_id,
        "user_id": request.user.pk,
    }


def issue_support_preview(request, video, context, *, media_expires_at):
    session = request.student_support_session
    expires_at = min(
        int(session.expires_at.timestamp()),
        int(timezone.now().timestamp()) + int(settings.VIDEO_PLAYBACK_TTL_SECONDS),
    )
    if media_expires_at is not None:
        expires_at = min(expires_at, media_expires_at)
    if expires_at <= int(timezone.now().timestamp()):
        raise PermissionDenied("지원 세션 또는 영상 권한이 만료되었습니다.")
    token = create_playback_token(
        payload={
            **support_preview_binding(request),
            "support_preview": True,
            "video_id": video.pk,
            "enrollment_id": context.enrollment.pk if context.enrollment else None,
            "inactive_entitlement_id": getattr(context.inactive_entitlement, "pk", None),
            "direct_entitlement_id": getattr(context.direct_entitlement, "pk", None),
            "event_protocol_version": 1,
        },
        expires_at=expires_at,
    )
    return token, expires_at
