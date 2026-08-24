"""Cross-domain dependencies for deferred student account notices."""

from __future__ import annotations

from typing import Any

from django.db.models import Q


def send_welcome_messages(**kwargs: Any) -> Any:
    from apps.domains.messaging.services import send_welcome_messages as _send

    return _send(**kwargs)


def active_student_account_outbox_exists(*, tenant_id: int, student_id: int) -> bool:
    from apps.domains.messaging.models import ScheduledNotification

    return (
        ScheduledNotification.objects.filter(
            status__in=[
                ScheduledNotification.Status.PENDING,
                ScheduledNotification.Status.DISPATCHING,
            ],
            payload__target_type="account",
            payload__target_id=f"student:{student_id}",
        )
        .filter(
            Q(payload__source_tenant_id=tenant_id)
            | Q(payload__source_tenant_id=str(tenant_id))
        )
        .exists()
    )
