from __future__ import annotations

import re
from datetime import datetime

from django.db.models import Q
from django.utils import timezone

from apps.domains.clinic.models import SessionParticipant
from apps.domains.clinic.time_ranges import participant_booking_window, session_window


def participant_tenant_scope(*, tenant) -> Q:
    """Require every populated booking relation to belong to the request tenant."""
    return (
        Q(student__tenant=tenant)
        & (Q(session__isnull=True) | Q(session__tenant=tenant))
        & (Q(enrollment__isnull=True) | Q(enrollment__tenant=tenant))
    )


def is_clinic_participant_reminder_active(*, participant_id: int, tenant_id: int) -> bool:
    """Return whether a manual reminder still belongs to an active booking."""
    return SessionParticipant.objects.filter(
        id=participant_id,
        tenant_id=tenant_id,
        status=SessionParticipant.Status.BOOKED,
        session__tenant_id=tenant_id,
        student__tenant_id=tenant_id,
        student__deleted_at__isnull=True,
        checked_in_at__isnull=True,
        checked_out_at__isnull=True,
    ).exists()


def is_clinic_booking_reminder_active(*, tenant_id: int, origin_id: str, now=None) -> bool:
    """Validate an exact time-range reminder again before queue/provider dispatch."""
    match = re.fullmatch(r"clinic_booking:(\d+):(\d+):(\d{8}):(\d{4})", origin_id)
    if not match:
        return False
    participant_id, session_id, date_text, time_text = match.groups()
    try:
        start = timezone.make_aware(datetime.strptime(date_text + time_text, "%Y%m%d%H%M"))
    except ValueError:
        return False
    if start <= (now or timezone.now()):
        return False
    participant = SessionParticipant.objects.select_related("session").filter(
        id=int(participant_id), tenant_id=tenant_id,
        session_id=int(session_id), session__tenant_id=tenant_id,
        session__booking_mode="time_range",
        booking_start_time=start.time(), booking_end_time__isnull=False,
        status=SessionParticipant.Status.BOOKED,
        student__tenant_id=tenant_id, student__deleted_at__isnull=True,
        checked_out_at__isnull=True,
    ).first()
    if not participant:
        return False
    session = participant.session
    opening, closing = session_window(session)
    booking_start, ending = participant_booking_window(participant)
    return booking_start == start.replace(tzinfo=None) and opening <= booking_start < ending <= closing


__all__ = [
    "is_clinic_participant_reminder_active",
    "is_clinic_booking_reminder_active",
    "participant_booking_window",
    "participant_tenant_scope",
]
