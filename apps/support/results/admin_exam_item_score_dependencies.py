"""Cross-domain dependencies for admin exam item scoring."""

from __future__ import annotations

from typing import Any

from apps.support.results.admin_exam_dependencies import (
    get_enrollment_for_tenant,
    get_regular_active_exam_for_tenant,
)


def get_answer_key_value(*, template_exam_id: int, question_id: int) -> Any | None:
    from apps.domains.exams.models import AnswerKey

    answer_key = AnswerKey.objects.filter(exam_id=template_exam_id).first()
    if not answer_key or not isinstance(answer_key.answers, dict):
        return None
    return answer_key.answers.get(str(question_id))


def get_exam_question_for_item_score(
    *,
    question_id: int,
    exam_ids: set[int],
    tenant: Any,
) -> Any | None:
    from apps.domains.exams.models import ExamQuestion

    return ExamQuestion.objects.filter(
        id=question_id,
        sheet__exam_id__in=exam_ids,
        sheet__exam__tenant=tenant,
    ).first()


def get_latest_exam_submission_id(*, enrollment_id: int, exam_id: int) -> int | None:
    from apps.domains.submissions.models import Submission

    submission = (
        Submission.objects.filter(
            enrollment_id=enrollment_id,
            target_type=Submission.TargetType.EXAM,
            target_id=exam_id,
        )
        .order_by("-id")
        .first()
    )
    return int(submission.id) if submission else None


def dispatch_progress_pipeline(**kwargs: Any) -> Any:
    from apps.domains.progress.dispatcher import dispatch_progress_pipeline as _dispatch

    return _dispatch(**kwargs)
