"""Cross-domain dependencies for result attempt services."""

from __future__ import annotations

from typing import Any


def exam_for_attempt_policy(*, exam_id: int) -> Any | None:
    from apps.domains.exams.models import Exam

    return Exam.objects.filter(id=int(exam_id)).first()


def submission_for_attempt(*, submission_id: int) -> Any | None:
    from apps.domains.submissions.models import Submission

    return Submission.objects.filter(id=int(submission_id)).first()


def clinic_link_for_attempt(*, clinic_link_id: int) -> Any | None:
    from apps.domains.progress.models import ClinicLink

    return (
        ClinicLink.objects
        .filter(id=int(clinic_link_id))
        .select_related("enrollment", "session", "session__lecture")
        .first()
    )
