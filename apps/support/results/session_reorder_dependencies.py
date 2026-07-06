"""Cross-domain dependencies for result session reorder views."""

from __future__ import annotations

from typing import Any

from django.shortcuts import get_object_or_404


def get_session_for_tenant(*, session_id: int, tenant: Any) -> Any:
    from apps.domains.lectures.models import Session

    return get_object_or_404(Session, id=session_id, lecture__tenant=tenant)


def reorder_session_exams(*, session: Any, ordered_ids: list[int]) -> None:
    from apps.domains.exams.models import Exam

    exams = list(Exam.objects.filter(id__in=ordered_ids, sessions=session))
    exam_map = {int(exam.id): exam for exam in exams}
    for index, exam_id in enumerate(ordered_ids):
        exam = exam_map.get(int(exam_id))
        if exam and exam.display_order != index:
            exam.display_order = index
            exam.save(update_fields=["display_order"])


def reorder_session_homeworks(*, session: Any, ordered_ids: list[int]) -> None:
    from apps.domains.homework_results.models import Homework

    homeworks = list(Homework.objects.filter(id__in=ordered_ids, session=session))
    homework_map = {int(homework.id): homework for homework in homeworks}
    for index, homework_id in enumerate(ordered_ids):
        homework = homework_map.get(int(homework_id))
        if homework and homework.display_order != index:
            homework.display_order = index
            homework.save(update_fields=["display_order"])
