"""Cross-domain serializer dependencies for enrollment."""

from __future__ import annotations


def lecture_queryset():
    from apps.domains.lectures.models import Lecture

    return Lecture.objects.all()


def session_queryset():
    from apps.domains.lectures.models import Session

    return Session.objects.select_related("lecture").all()
