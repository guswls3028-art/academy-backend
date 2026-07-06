"""Cross-domain dependencies for community QnA notifications."""

from __future__ import annotations

from typing import Any


def resolve_qna_freeform_template(tenant_id: int) -> Any | None:
    from apps.domains.messaging.selectors import resolve_freeform_template

    return resolve_freeform_template(tenant_id)


def enqueue_qna_sms(**kwargs: Any) -> Any:
    from apps.domains.messaging.services import enqueue_sms

    return enqueue_sms(**kwargs)


def qna_tenant_site_url(tenant: Any) -> str | None:
    from apps.domains.messaging.services.url_helpers import get_tenant_site_url

    return get_tenant_site_url(tenant)


def build_fallback_qna_replacements(
    *,
    body: str,
    lecture_label: str,
    session_label: str,
    date_label: str,
    time_label: str,
    academy_name: str,
    student_name: str,
    site_url: str,
) -> tuple[str, list[dict[str, str]]]:
    from apps.domains.messaging.alimtalk_content_builders import (
        SOLAPI_ATTENDANCE,
        TYPE_ATTENDANCE,
        build_manual_replacements,
    )

    replacements = build_manual_replacements(
        TYPE_ATTENDANCE,
        body,
        {
            "강의명": lecture_label,
            "차시명": session_label,
            "날짜": date_label,
            "시간": time_label,
        },
        tenant_name=academy_name,
        student_name=student_name,
        site_url=site_url,
    )
    return SOLAPI_ATTENDANCE, replacements


def active_staff_profiles_for_qna(tenant: Any) -> Any:
    from apps.domains.staffs.models import Staff

    return Staff.objects.filter(tenant=tenant, is_active=True).only("id", "name", "phone")
