"""Cross-domain read dependencies for clinic drift maintenance commands."""

from __future__ import annotations


def valid_exam_ids(exam_ids: set[int]) -> set[int]:
    if not exam_ids:
        return set()
    from apps.domains.exams.models import Exam

    return set(Exam.objects.filter(id__in=exam_ids).values_list("id", flat=True))


def valid_homework_ids(homework_ids: set[int]) -> set[int]:
    if not homework_ids:
        return set()
    from apps.domains.homework_results.models import Homework

    return set(Homework.objects.filter(id__in=homework_ids).values_list("id", flat=True))


def exam_ids_by_session(session_ids: set[int]) -> dict[int, list[int]]:
    if not session_ids:
        return {}
    from apps.domains.exams.models import Exam

    session_exam_map: dict[int, list[int]] = {}
    for exam_id, session_id in (
        Exam.objects
        .filter(sessions__id__in=session_ids)
        .values_list("id", "sessions")
    ):
        if session_id is None:
            continue
        session_exam_map.setdefault(int(session_id), []).append(int(exam_id))
    return session_exam_map


def enrollment_ids_for_tenant(tenant_id: int) -> set[int]:
    from apps.domains.enrollment.models import Enrollment

    return set(Enrollment.objects.filter(tenant_id=tenant_id).values_list("id", flat=True))


def exam_attempt_queryset():
    from apps.domains.results.models import ExamAttempt

    return ExamAttempt.objects
