"""Public test-fixture helpers for domains that consume student identities."""

from apps.domains.students.models import Student
from apps.domains.students.services.creation import create_student_account


def create_student_fixture(**kwargs) -> Student:
    """Create a Student while keeping cross-domain tests off internal models."""
    return Student.objects.create(**kwargs)


def create_student_account_fixture(**kwargs) -> Student:
    """Create a student account through the owning domain's production service."""
    return create_student_account(**kwargs).student
