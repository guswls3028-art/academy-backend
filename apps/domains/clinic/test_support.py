"""Public test-fixture helpers for domains that consume clinic records."""

from apps.domains.clinic.models import Session, SessionParticipant


def create_clinic_session_fixture(**kwargs) -> Session:
    return Session.objects.create(**kwargs)


def create_clinic_participant_fixture(**kwargs) -> SessionParticipant:
    return SessionParticipant.objects.create(**kwargs)
