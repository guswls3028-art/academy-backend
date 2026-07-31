"""Public test-fixture helpers for domains that consume lectures and sessions."""

from apps.domains.lectures.models import Lecture, Session


def create_lecture_fixture(**kwargs) -> Lecture:
    """Create a Lecture without exposing domain-internal models to tests."""
    return Lecture.objects.create(**kwargs)


def create_session_fixture(**kwargs) -> Session:
    """Create a Session without exposing domain-internal models to tests."""
    return Session.objects.create(**kwargs)
