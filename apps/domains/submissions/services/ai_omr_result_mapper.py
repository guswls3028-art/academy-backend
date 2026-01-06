# apps/domains/submissions/services/ai_omr_result_mapper.py
from __future__ import annotations

from typing import Any, Dict, Optional
import logging

from django.db import transaction   # ✅ 이 줄 추가

from apps.domains.submissions.models import Submission, SubmissionAnswer

logger = logging.getLogger(__name__)


@transaction.atomic
def apply_omr_ai_result(payload: Dict[str, Any]) -> Optional[int]:
    """
    FINAL CONTRACT:
    - answers[*].exam_question_id 필수
    """

    submission_id = payload.get("submission_id")
    if not submission_id:
        return None

    try:
        submission = Submission.objects.select_for_update().get(id=int(submission_id))
    except Submission.DoesNotExist:
        return None

    base_payload = submission.payload or {}
    base_payload["ai_result"] = payload
    submission.payload = base_payload

    status = payload.get("status")
    if status == "FAILED":
        submission.status = Submission.Status.FAILED
        submission.error_message = payload.get("error") or "AI worker failed"
        submission.save(update_fields=["payload", "status", "error_message", "updated_at"])
        return submission.id

    result = payload.get("result") or {}
    answers = result.get("answers") or []

    for a in answers:
        eqid = a.get("exam_question_id")
        if not eqid:
            continue

        SubmissionAnswer.objects.update_or_create(
            submission=submission,
            exam_question_id=int(eqid),
            defaults={
                "answer": "".join([str(x) for x in a.get("detected") or []]),
                "meta": {
                    "omr": {
                        "version": result.get("version"),
                        "detected": a.get("detected"),
                        "marking": a.get("marking"),
                        "confidence": a.get("confidence"),
                        "status": a.get("status"),
                    }
                },
            },
        )

    submission.status = Submission.Status.ANSWERS_READY
    submission.error_message = ""
    submission.save(update_fields=["payload", "status", "error_message", "updated_at"])
    return submission.id
