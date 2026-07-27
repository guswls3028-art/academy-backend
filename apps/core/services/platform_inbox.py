from __future__ import annotations

from django.conf import settings
from django.db.models import F, Max, Q

from apps.core.models import LandingConsultRequest, OpsAuditLog


PROMO_LEAD_SOURCES = frozenset({"promo-contact", "promo-demo"})
INCIDENT_STATUS_ACTION = "inbox.incident_status"


def platform_inbox_summary() -> dict[str, int]:
    from apps.domains.community.models import (
        PostEntity,
        platform_support_kind_q,
        platform_support_q,
    )

    support_posts = (
        PostEntity.objects.filter(post_type="board")
        .filter(platform_support_q())
        .annotate(
            _latest_platform_reply=Max(
                "replies__created_at",
                filter=Q(replies__author_role="platform_staff"),
            ),
            _latest_requester_reply=Max(
                "replies__created_at",
                filter=~Q(replies__author_role="platform_staff"),
            ),
        )
    )
    owner_tenant_id = getattr(settings, "OWNER_TENANT_ID", None)
    leads = LandingConsultRequest.objects.none()
    if owner_tenant_id is not None:
        leads = LandingConsultRequest.objects.filter(
            tenant_id=owner_tenant_id,
            source__in=PROMO_LEAD_SOURCES,
        )

    incidents = OpsAuditLog.objects.filter(action="user_incident.manual")
    support_total = support_posts.count()
    support_open = support_posts.filter(
        Q(_latest_platform_reply__isnull=True)
        | Q(_latest_requester_reply__gt=F("_latest_platform_reply"))
    ).count()
    lead_total = leads.count()
    lead_open = leads.filter(resolved_at__isnull=True).count()
    incident_total = incidents.count()
    incident_resolved = incidents.filter(inbox_state__status="resolved").count()
    incident_open = incident_total - incident_resolved
    bugs = (
        support_posts.filter(platform_support_kind_q("bug")).count()
        + incident_total
    )
    feedbacks = support_posts.filter(platform_support_kind_q("feedback")).count()
    total = support_total + lead_total + incident_total
    open_count = support_open + lead_open + incident_open
    return {
        "total": total,
        "open": open_count,
        "resolved": total - open_count,
        "bugs": bugs,
        "feedbacks": feedbacks,
        "contacts": lead_total,
    }
