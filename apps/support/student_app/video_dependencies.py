"""Cross-domain dependency loaders for student video views."""

from __future__ import annotations

from typing import Any


def active_enrollments_for_student(*, tenant: Any, student: Any, include_system: bool = False):
    from apps.domains.enrollment.selectors import active_enrollments_for_student as select

    return select(tenant=tenant, student=student, include_system=include_system)


def get_media_models():
    try:
        from apps.domains.video.models import Video, VideoAccess
    except Exception as exc:
        raise RuntimeError(
            "[CRITICAL] apps.domains.video.models.Video import 실패"
        ) from exc
    return Video, VideoAccess


def get_lecture_models():
    from apps.domains.lectures.models import Lecture, Session

    return Lecture, Session


def get_video_model():
    from apps.domains.video.models import Video

    return Video


def get_video_progress_model():
    from apps.domains.video.models import VideoProgress

    return VideoProgress


def get_video_access_mode():
    from apps.domains.video.models import AccessMode

    return AccessMode


def get_inactive_video_entitlement_error_type():
    from apps.domains.video.services.inactive_entitlements import (
        InactiveVideoEntitlementError,
    )

    return InactiveVideoEntitlementError


def active_inactive_video_entitlements_for_student(**kwargs):
    from apps.domains.video.services.inactive_entitlements import (
        active_entitlements_for_student,
    )

    return active_entitlements_for_student(**kwargs)


def resolve_active_inactive_video_entitlement(**kwargs):
    from apps.domains.video.services.inactive_entitlements import (
        get_active_inactive_video_entitlement,
    )

    return get_active_inactive_video_entitlement(**kwargs)


def update_inactive_entitled_video_progress(**kwargs):
    from apps.domains.video.services.inactive_entitlements import (
        update_inactive_entitled_video_progress as update,
    )

    return update(**kwargs)


def get_video_like_models():
    from apps.domains.video.models import Video, VideoLike

    return Video, VideoLike


def get_video_comment_models():
    from apps.domains.video.models import Video, VideoComment

    return Video, VideoComment


def resolve_access_modes_for_videos_prefetched(**kwargs):
    from apps.domains.video.services.access_resolver import (
        resolve_access_modes_for_videos_prefetched as resolve,
    )

    return resolve(**kwargs)
