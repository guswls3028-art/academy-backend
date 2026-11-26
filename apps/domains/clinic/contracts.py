from __future__ import annotations

import re
from datetime import datetime

from django.db.models import Q
from django.utils import timezone

from apps.domains.clinic.models import SessionParticipant
from apps.domains.clinic.time_ranges import participant_booking_window, session_window
from apps.shared.utils.student_contact import contact_phone


def clinic_notification_teachers(tenant_id):
    from django.contrib.auth import get_user_model

    return get_user_model().objects.filter(
        is_active=True, tenant_memberships__tenant_id=tenant_id,
        tenant_memberships__is_active=True,
        tenant_memberships__role__in=("owner", "admin", "staff", "teacher"),
    ).order_by("name", "id")


def teacher_mobile(user):
    phone = contact_phone(user.phone) if user else ""
    return phone if len(phone) == 11 and phone.startswith("010") else ""


def booking_notice_teacher(tenant_id, participant_id, user_id):
    if not SessionParticipant.objects.filter(
        pk=participant_id, tenant_id=tenant_id, student__tenant_id=tenant_id,
        student__deleted_at__isnull=True, session__tenant_id=tenant_id,
        session__notification_teacher_id=user_id,
        source=SessionParticipant.Source.STUDENT_REQUEST, status__in=("pending", "booked"),
    ).exists():
        return None
    return clinic_notification_teachers(tenant_id).filter(pk=user_id).first()


def booking_notice_is_current(payload, tenant_id):
    """Revalidate an assigned teacher and the receipt state just before delivery."""
    teacher_copy = payload.get("target_type") == "clinic_teacher"
    reference = str(payload.get("domain_object_id") or "")
    match = re.fullmatch(r"clinic_participant:([1-9][0-9]*):(reservation_pending|reservation|approval)", reference)
    if not match:
        return not teacher_copy
    participant_id, event = match.groups()
    if teacher_copy:
        user_id = str(payload.get("target_id") or "")
        if not user_id.isascii() or not user_id.isdigit():
            return False
        teacher = booking_notice_teacher(tenant_id, participant_id, user_id)
        phone = teacher_mobile(teacher)
        if not phone or phone != contact_phone(payload.get("to")):
            return False
    if event == "reservation" and not teacher_copy:
        return True  # Preserve legacy receipts that had no state discriminator.
    return SessionParticipant.objects.filter(
        pk=participant_id, tenant_id=tenant_id, student__tenant_id=tenant_id,
        student__deleted_at__isnull=True, session__tenant_id=tenant_id,
        status="pending" if event == "reservation_pending" else "booked",
    ).exists()


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
