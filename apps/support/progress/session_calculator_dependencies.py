"""Cross-domain read helpers for session progress calculation."""

from __future__ import annotations


def get_result_attempt_models():
    from apps.domains.results.models import ExamAttempt, Result

    return Result, ExamAttempt


def get_exam_model():
    from apps.domains.exams.models import Exam

    return Exam


def get_exam_ids_for_session(session) -> list[int]:
    from apps.domains.results.utils.session_exam import (
        get_exam_ids_for_session as get_ids,
    )

    return get_ids_for_session_as_ints(get_ids(session))


def get_ids_for_session_as_ints(exam_ids) -> list[int]:
    return [int(exam_id) for exam_id in exam_ids]


def homework_score_exists(**filters) -> bool:
    from apps.domains.homework_results.models import HomeworkScore

    return HomeworkScore.objects.filter(**filters).exists()
