# apps/domains/submissions/services/ai_result_mapper.py
from __future__ import annotations

from typing import Any, Dict, Optional
import logging

from django.db import transaction

from apps.domains.submissions.models import Submission, SubmissionAnswer

logger = logging.getLogger(__name__)


@transaction.atomic
def apply_ai_result(payload: Dict[str, Any]) -> Optional[int]:
    """
    Worker AI 결과를 submissions에 반영.
    """
    submission_id = payload.get("submission_id")
    if not submission_id:
        return None

    # ✅ 보호패치: submission 없으면 DROP
    try:
        submission = Submission.objects.select_for_update().get(
            id=int(submission_id)
        )
    except Submission.DoesNotExist:
        logger.warning(
            "[AI_RESULT] submission %s not found. result dropped.",
            submission_id,
        )
        return None

    # ✅ 결과 원본 저장 (항상)
    base_payload = submission.payload or {}
    base_payload["ai_result"] = payload
    submission.payload = base_payload

    status = payload.get("status")
    error = payload.get("error")

    # ✅ FAILED 결과 즉시 처리
    if status == "FAILED":
        submission.status = Submission.Status.FAILED
        submission.error_message = error or "AI worker failed"
        submission.save(
            update_fields=[
                "payload",
                "status",
                "error_message",
                "updated_at",
            ]
        )
        return submission.id

    # =========================
    # 1) items 답안형
    # =========================
    items = payload.get("result", {}).get("items")
    if isinstance(items, list) and items:
        for item in items:
            qid = item.get("question_id")
            if not qid:
                continue

            SubmissionAnswer.objects.update_or_create(
                submission=submission,
                question_id=int(qid),
                defaults={
                    "answer": str(item.get("answer") or ""),
                    "meta": item.get("meta") or None,
                },
            )

        submission.status = Submission.Status.ANSWERS_READY
        submission.error_message = ""
        meta = submission.meta or {}
        meta["answers_ready"] = True
        meta["answer_count"] = SubmissionAnswer.objects.filter(
            submission=submission
        ).count()
        submission.meta = meta
        submission.save(
            update_fields=[
                "payload",
                "status",
                "error_message",
                "meta",
                "updated_at",
            ]
        )
        return submission.id

    # =========================
    # 2) OMR v1 형식
    # =========================
    result = payload.get("result") or {}
    answers = result.get("answers")
    version = result.get("version")

    if version and isinstance(answers, list) and answers:
        for a in answers:
            qid = (a or {}).get("question_id")
            if not qid:
                continue

            detected = (a or {}).get("detected") or []
            ans_text = (
                "".join([str(x).strip().upper() for x in detected])
                if isinstance(detected, list)
                else ""
            )

            SubmissionAnswer.objects.update_or_create(
                submission=submission,
                question_id=int(qid),
                defaults={
                    "answer": ans_text,
                    "meta": {"omr": a},
                },
            )

        submission.status = Submission.Status.ANSWERS_READY
        submission.error_message = ""
        meta = submission.meta or {}
        meta["answers_ready"] = True
        meta["answer_count"] = SubmissionAnswer.objects.filter(
            submission=submission
        ).count()
        meta["omr_version"] = str(version)
        submission.meta = meta
        submission.save(
            update_fields=[
                "payload",
                "status",
                "error_message",
                "meta",
                "updated_at",
            ]
        )
        return submission.id

    # =========================
    # 3) 분석형
    # =========================
    analysis = result.get("analysis")
    if analysis is not None:
        meta = submission.meta or {}
        meta["analysis"] = analysis
        submission.meta = meta

        if submission.target_type == Submission.TargetType.HOMEWORK:
            submission.status = Submission.Status.DONE

        submission.save(
            update_fields=[
                "payload",
                "meta",
                "status",
                "updated_at",
            ]
        )
        return None

    submission.save(update_fields=["payload", "updated_at"])
    return None
