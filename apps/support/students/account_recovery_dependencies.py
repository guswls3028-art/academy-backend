"""Cross-domain dependencies for student account recovery."""

from __future__ import annotations

from typing import Any

from django.conf import settings


def ensure_parent_recovery_account(
    *,
    tenant: Any,
    parent_phone: str,
    student_name: str,
) -> Any | None:
    from apps.domains.parents.models import Parent
    from apps.domains.parents.services import ensure_parent_for_student

    ensure_parent_for_student(
        tenant=tenant,
        parent_phone=parent_phone,
        student_name=student_name,
    )
    return (
        Parent.objects
        .filter(tenant=tenant, phone=parent_phone)
        .select_related("user")
        .first()
    )


def account_recovery_delivery_disabled(source_tenant_id: int) -> bool:
    from apps.domains.messaging.policy import is_messaging_disabled

    owner_tenant_id = getattr(settings, "OWNER_TENANT_ID", None)
    return is_messaging_disabled(source_tenant_id) or (
        bool(owner_tenant_id) and is_messaging_disabled(owner_tenant_id)
    )


def send_account_recovery_alimtalk(**kwargs: Any) -> bool:
    from apps.domains.messaging.policy import send_alimtalk_via_owner

    return send_alimtalk_via_owner(**kwargs)
