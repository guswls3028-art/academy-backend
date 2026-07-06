"""Cross-domain dependencies for result attempt services."""

from __future__ import annotations

from typing import Any


def exam_for_attempt_policy(*, exam_id: int) -> Any | None:
    from apps.domains.exams.models import Exam

    return Exam.objects.filter(id=int(exam_id)).first()

