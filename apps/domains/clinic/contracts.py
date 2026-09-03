from __future__ import annotations

from apps.domains.clinic.models import SessionParticipant


def is_clinic_participant_reminder_active(*, participant_id: int, tenant_id: int) -> bool:
    """Return whether a manual reminder still belongs to an active booking."""
    return SessionParticipant.objects.filter(
        id=participant_id,
        tenant_id=tenant_id,
        status=SessionParticipant.Status.BOOKED,
        session__isnull=False,
        student__deleted_at__isnull=True,
    ).exists()


__all__ = ["is_clinic_participant_reminder_active"]
