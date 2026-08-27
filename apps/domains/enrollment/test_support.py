"""Public test-fixture helpers for domains that consume enrollments."""

from apps.domains.enrollment.models import Enrollment, SessionEnrollment


def create_enrollment_fixture(**kwargs) -> Enrollment:
    """Create an Enrollment without exposing domain-internal models to tests."""
    return Enrollment.objects.create(**kwargs)


def get_enrollment_fixture(**kwargs) -> Enrollment:
    """Reload an Enrollment for a cross-domain concurrency thread."""
    return Enrollment.objects.get(**kwargs)


def create_session_enrollment_fixture(**kwargs) -> SessionEnrollment:
    """Create exact session scope without exposing the internal model."""
    return SessionEnrollment.objects.create(**kwargs)
