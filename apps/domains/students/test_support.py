"""Public test-fixture helpers for domains that consume student identities."""

from apps.domains.students.models import Student


def create_student_fixture(**kwargs) -> Student:
    """Create a Student while keeping cross-domain tests off internal models."""
    return Student.objects.create(**kwargs)
