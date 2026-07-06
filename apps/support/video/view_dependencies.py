"""Cross-domain dependencies for video views."""

from __future__ import annotations

from typing import Any


def lock_session_for_video_upload(session: Any) -> Any:
    from apps.domains.lectures.models import Session

    return Session.objects.select_for_update().select_related("lecture__tenant").get(pk=session.pk)


def get_or_create_public_video_session(*, tenant: Any) -> tuple[Any, Any]:
    from apps.domains.lectures.models import Lecture, Session

    lecture = Lecture.get_or_create_system_lecture(tenant)
    session, _ = Session.objects.get_or_create(
        lecture=lecture,
        order=1,
        defaults={"title": "전체공개영상", "date": None},
    )
    return lecture, session


def get_staff_for_video_upload(*, user: Any, tenant: Any) -> Any | None:
    from apps.domains.staffs.models import Staff

    return Staff.objects.filter(user=user, tenant=tenant).first()


def clinic_highlight_map_for_video_stats(*, tenant: Any, enrollment_ids: set[int]) -> dict[int, bool]:
    from apps.domains.results.utils.clinic_highlight import compute_clinic_highlight_map

    if not tenant or not enrollment_ids:
        return {}
    return compute_clinic_highlight_map(
        tenant=tenant,
        enrollment_ids=enrollment_ids,
    )
