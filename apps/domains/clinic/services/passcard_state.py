"""Shared booking predicates for clinic passcard and yellow highlighting."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from django.db.models import Q
from django.utils import timezone

from apps.domains.clinic.models import SessionParticipant


def _scheduled_booking_q(*, statuses: Iterable[str], local_date) -> Q:
    return Q(status__in=tuple(statuses)) & (
        Q(session__date__gte=local_date)
        | Q(session__isnull=True, requested_date__gte=local_date)
    )


def passcard_tenant_booking_q(*, tenant: Any) -> Q:
    """Require every populated booking relation to remain in the request tenant."""
    return (
        Q(student__tenant=tenant)
        & (Q(session__isnull=True) | Q(session__tenant=tenant))
        & (Q(enrollment__isnull=True) | Q(enrollment__tenant=tenant))
    )


def passcard_visible_booking_q(*, local_date=None) -> Q:
    """Bookings that remain visible in the student passcard projection."""
    effective_date = local_date or timezone.localdate()
    return (_scheduled_booking_q(
        statuses=(
            SessionParticipant.Status.PENDING,
            SessionParticipant.Status.BOOKED,
        ),
        local_date=effective_date,
    ) & Q(completed_at__isnull=True)) | Q(
        status=SessionParticipant.Status.ATTENDED,
        completed_at__isnull=True,
    )


def passcard_confirming_booking_q(*, local_date=None) -> Q:
    """Bookings that change CLINIC_REQUIRED to BOOKING_CONFIRMED."""
    effective_date = local_date or timezone.localdate()
    return (_scheduled_booking_q(
        statuses=(SessionParticipant.Status.BOOKED,),
        local_date=effective_date,
    ) & Q(completed_at__isnull=True)) | Q(
        status=SessionParticipant.Status.ATTENDED,
        completed_at__isnull=True,
    )


def passcard_confirmed_student_ids(
    *,
    tenant: Any,
    student_ids: Iterable[int],
    local_date=None,
) -> set[int]:
    """Return tenant-scoped students whose passcard is reservation-confirmed."""
    normalized_ids = {int(student_id) for student_id in student_ids if student_id}
    if not normalized_ids:
        return set()
    return set(
        SessionParticipant.objects.filter(
            tenant=tenant,
            student_id__in=normalized_ids,
        )
        .filter(passcard_tenant_booking_q(tenant=tenant))
        .filter(passcard_confirming_booking_q(local_date=local_date))
        .values_list("student_id", flat=True)
        .distinct()
    )
