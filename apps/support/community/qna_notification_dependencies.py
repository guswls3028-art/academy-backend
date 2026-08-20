"""Cross-domain dependencies for community QnA notifications."""

from __future__ import annotations

from typing import Any


def resolve_qna_freeform_template(tenant_id: int) -> Any | None:
    # Community/QnA has no approved Kakao envelope mapping in the common Solapi channel.
    # Keep external Alimtalk fail-closed instead of falling back to a generic/freeform template.
    return None


def enqueue_qna_alimtalk(**kwargs: Any) -> Any:
    from apps.domains.messaging.models import ScheduledNotification
    from apps.domains.messaging.scheduled import dispatch_notification_now

    tenant_id = int(kwargs["tenant_id"])
    trigger = str(kwargs.get("event_type") or "qna_notification")
    notification = dispatch_notification_now(
        tenant_id=tenant_id,
        trigger=trigger,
        payload=kwargs,
    )
    return notification.status in {
        ScheduledNotification.Status.PENDING,
        ScheduledNotification.Status.SENT,
    }


def qna_tenant_site_url(tenant: Any) -> str | None:
    from apps.domains.messaging.services.url_helpers import get_tenant_site_url

    return get_tenant_site_url(tenant)


def active_staff_profiles_for_qna(tenant: Any) -> Any:
    from apps.domains.staffs.models import Staff

    return Staff.objects.filter(tenant=tenant, is_active=True).only("id", "name", "phone")
