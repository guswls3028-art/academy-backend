"""Cross-domain model boundary for inactive video entitlement services."""

from __future__ import annotations


def get_inactive_entitlement_scope_models():
    from apps.domains.enrollment.models import Enrollment, SessionEnrollment
    from apps.domains.lectures.models import Lecture, Session
    from apps.domains.students.models import Student

    return Enrollment, SessionEnrollment, Lecture, Session, Student
