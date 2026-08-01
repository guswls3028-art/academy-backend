"""Cross-domain dependencies for the attendance arrival projection."""

from __future__ import annotations

from django.db.models import Q


def clinic_arrival_participants_for_tenant(
    *,
    tenant,
    start_date,
    end_date,
    statuses: set[str],
):
    from apps.domains.clinic.models import SessionParticipant

    return (
        SessionParticipant.objects
        .filter(tenant=tenant, status__in=statuses)
        .filter(
            Q(session__date__range=(start_date, end_date))
            | Q(
                session__isnull=True,
                requested_date__range=(start_date, end_date),
            )
        )
        .select_related("session", "student", "enrollment", "enrollment__lecture")
    )
