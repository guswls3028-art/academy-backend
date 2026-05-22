from __future__ import annotations

from apps.domains.clinic.models import SessionParticipant


def cancel_active_participants_for_student(*, tenant, student, changed_at) -> int:
    return SessionParticipant.objects.filter(
        student=student,
        tenant=tenant,
        status__in=[SessionParticipant.Status.PENDING, SessionParticipant.Status.BOOKED],
    ).update(status=SessionParticipant.Status.CANCELLED, status_changed_at=changed_at)
