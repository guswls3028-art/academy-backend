from __future__ import annotations

from typing import Any, Dict, Optional
import logging
from datetime import datetime, timezone

from django.db import transaction

from apps.domains.submissions.models import Submission, SubmissionAnswer

logger = logging.getLogger(__name__)


@transaction.atomic
def apply_omr_ai_result(payload: Dict[str, Any]) -> Optional[int]:
    submission_id = payload.get("submission_id")
    if not submission_id:
        return None

    try:
        submission = Submission.objects.select_for_update().get(id=int(submission_id))
    except Submission.DoesNotExist:
        return None

    status = payload.get("status")
    result = payload.get("result") or {}
    error = payload.get("error")

    meta = dict(submission.meta or {})
    meta["ai_result"] = {
        "status": status,
        "result": result,
        "error": error,
        "received_at": datetime.now(timezone.utc).isoformat(),
        "kind": "omr_scan",
    }
    submission.meta = meta

    if status == "FAILED":
        submission.status = Submission.Status.FAILED
        submission.error_message = error or "AI worker failed"
        submission.save(update_fields=["meta", "status", "error_message", "updated_at"])
        return submission.id

    answers = result.get("answers") or []
    identifier = result.get("identifier")

    manual_required = False
    reasons = []

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

        st = str(a.get("status") or "").lower()
        mk = str(a.get("marking") or "").lower()
        conf = a.get("confidence")

        try:
            conf_f = float(conf) if conf is not None else None
        except Exception:
            conf_f = None

        if st != "ok":
            manual_required = True
            reasons.append("ANSWER_STATUS_NOT_OK")

        if mk in ("blank", "multi"):
            manual_required = True
            reasons.append("ANSWER_BLANK_OR_MULTI")

        if conf_f is not None and conf_f < 0.70:
            manual_required = True
            reasons.append("ANSWER_LOW_CONFIDENCE")

    # ✅ 식별자 실패 → 명시적 상태
    identifier_ok = (
        isinstance(identifier, dict)
        and identifier.get("status") == "ok"
        and identifier.get("enrollment_id")
    )

    meta.setdefault("manual_review", {})
    meta["manual_review"]["required"] = bool(manual_required or not identifier_ok)
    meta["manual_review"]["reasons"] = sorted(set(reasons))
    meta["manual_review"]["updated_at"] = datetime.now(timezone.utc).isoformat()

    submission.meta = meta

    if not identifier_ok:
        submission.status = Submission.Status.NEEDS_IDENTIFICATION
        submission.error_message = ""
        submission.save(update_fields=["meta", "status", "error_message", "updated_at"])
        return submission.id

    submission.enrollment_id = int(identifier.get("enrollment_id"))
    submission.status = Submission.Status.ANSWERS_READY
    submission.error_message = ""
    submission.save(update_fields=["meta", "status", "enrollment_id", "error_message", "updated_at"])

    return submission.id
