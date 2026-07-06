"""Cross-domain dependency loaders for clinic remediation."""

from __future__ import annotations

from typing import Any


def get_exam_retake_models():
    from apps.domains.exams.models import Exam
    from apps.domains.results.models import ExamAttempt

    return ExamAttempt, Exam


def get_homework_retake_models():
    from apps.domains.homework_results.models import Homework, HomeworkScore

    return HomeworkScore, Homework


def calc_homework_passed_and_clinic(*, session: Any, score: float, max_score: float):
    from apps.domains.homework.utils.homework_policy import (
        calc_homework_passed_and_clinic as calculate,
    )

    return calculate(session=session, score=score, max_score=max_score)
