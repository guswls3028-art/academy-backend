"""Public test-fixture helpers for domains that consume enrollments."""

from apps.domains.enrollment.models import Enrollment


def create_enrollment_fixture(**kwargs) -> Enrollment:
    """Create an Enrollment without exposing domain-internal models to tests."""
    return Enrollment.objects.create(**kwargs)
