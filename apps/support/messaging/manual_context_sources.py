from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from apps.domains.clinic.models import Session
from apps.domains.clinic.services.lifecycle import (
    build_session_change_notification_context,
    session_change_notice_student_ids,
)


class ManualContextSourceError(ValueError):
    pass


@dataclass(frozen=True)
class ManualContextSourceResult:
    student_ids: list[int]
    context: dict[str, Any]


def _parse_positive_int(value) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def resolve_manual_notification_context_source(
    *,
    tenant,
    trigger: str,
    context_source,
    actor=None,
) -> ManualContextSourceResult:
    if not isinstance(context_source, dict):
        raise ManualContextSourceError("context_source는 객체여야 합니다.")

    source_type = context_source.get("type")
    if source_type != "clinic_session_change":
        raise ManualContextSourceError("지원하지 않는 context_source입니다.")
    if trigger != "clinic_reservation_changed":
        raise ManualContextSourceError("clinic_session_change는 클리닉 변경 알림에만 사용할 수 있습니다.")

    session_id = _parse_positive_int(context_source.get("session_id"))
    if session_id is None:
        raise ManualContextSourceError("context_source.session_id는 양의 정수여야 합니다.")

    try:
        session = Session.objects.get(pk=session_id, tenant=tenant)
    except Session.DoesNotExist as exc:
        raise ManualContextSourceError("클리닉 세션을 찾을 수 없습니다.") from exc

    return ManualContextSourceResult(
        student_ids=session_change_notice_student_ids(tenant=tenant, session=session),
        context=build_session_change_notification_context(session=session, actor=actor),
    )
