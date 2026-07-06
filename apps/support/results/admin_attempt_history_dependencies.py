"""Cross-domain dependency loaders for admin attempt history."""

from __future__ import annotations

from typing import Any


def enrollment_belongs_to_tenant(*, enrollment_id: int, tenant: Any) -> bool:
    from apps.domains.enrollment.models import Enrollment

    return Enrollment.objects.filter(id=enrollment_id, tenant=tenant).exists()


def get_exam_history_models():
    from apps.domains.exams.models import Exam
    from apps.domains.progress.models import ClinicLink
    from apps.domains.results.models import ExamAttempt, Result

    return Exam, ExamAttempt, Result, ClinicLink


def get_homework_history_models():
    from apps.domains.homework.models.homework_policy import HomeworkPolicy
    from apps.domains.homework_results.models.homework import Homework
    from apps.domains.homework_results.models.score import HomeworkScore
    from apps.domains.progress.models import ClinicLink

    return Homework, HomeworkScore, HomeworkPolicy, ClinicLink
