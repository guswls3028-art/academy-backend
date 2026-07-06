"""Cross-domain helpers for enrollment lifecycle use cases."""

from __future__ import annotations

from typing import Any


def ensure_session_roster_membership(*, tenant: Any, session: Any, enrollment: Any):
    from apps.domains.attendance.services import (
        ensure_session_roster_membership as ensure,
    )

    return ensure(tenant=tenant, session=session, enrollment=enrollment)


def auto_assign_fees_on_enrollment(tenant: Any, student: Any, lecture: Any, enrollment: Any):
    from apps.domains.fees.services import auto_assign_fees_on_enrollment as assign

    return assign(tenant, student, lecture, enrollment)


def deactivate_fees_for_enrollment(enrollment: Any):
    from apps.domains.fees.services import deactivate_fees_for_enrollment as deactivate

    return deactivate(enrollment)


def send_event_notification(**kwargs):
    from apps.domains.messaging.services import send_event_notification as send

    return send(**kwargs)


def get_exam_learning_access_models():
    from apps.domains.exams.models import Exam, ExamEnrollment

    return Exam, ExamEnrollment


def get_homework_learning_access_models():
    from apps.domains.homework.models import HomeworkAssignment
    from apps.domains.homework_results.models import Homework

    return HomeworkAssignment, Homework
