"""Public test-fixture helpers for domains that consume homework results."""

from apps.domains.homework_results.models import Homework, HomeworkScore


def create_homework_fixture(**kwargs) -> Homework:
    """Create a homework definition without exposing the internal model."""
    return Homework.objects.create(**kwargs)


def create_homework_score_fixture(**kwargs) -> HomeworkScore:
    """Create a teacher-owned homework score for integration tests."""
    return HomeworkScore.objects.create(**kwargs)
