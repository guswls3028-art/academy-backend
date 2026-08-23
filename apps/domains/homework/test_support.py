"""Public test-fixture helpers for domains that consume homework assignments."""

from apps.domains.homework.models import HomeworkAssignment


def create_homework_assignment_fixture(**kwargs) -> HomeworkAssignment:
    """Create a homework assignment without exposing the internal model."""
    return HomeworkAssignment.objects.create(**kwargs)
