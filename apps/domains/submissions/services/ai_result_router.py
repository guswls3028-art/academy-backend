from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional
import logging

from django.db import transaction

from apps.domains.submissions.models import Submission
from apps.domains.submissions.services.ai_omr_result_mapper import apply_omr_ai_result

logger = logging.getLogger(__name__)

MIN_HOMEWORK_VIDEO_FILLED_RATIO = 0.10


@dataclass(frozen=True)
class ApplyAIResultOutcome:
    returned_submission_id: Optional[int]
    should_grade: bool
    detail: Dict[str, Any]


def _coerce_float(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except Exception:
        return default


def _safe_bool(v: Any, default: bool = False) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return bool(v)
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "yes", "y", "on")
    return default


@transaction.atomic
def apply_ai_result_for_submission(
    *,
    submission_id: int,
    status: str,
    result: Optional[Dict[str, Any]],
    error: Optional[str],
) -> ApplyAIResultOutcome:
    try:
        submission = Submission.objects.select_for_update().get(id=int(submission_id))
    except Submission.DoesNotExist:
        return ApplyAIResultOutcome(None, False, {"error": "submission not found"})

    meta = dict(submission.meta or {})
    meta["ai_result"] = {"status": status, "result": result, "error": error}
    submission.meta = meta

    if str(status).upper() == "FAILED":
        submission.status = Submission.Status.FAILED
        submission.error_message = error or "AI worker failed"
        submission.save(update_fields=["meta", "status", "error_message", "updated_at"])
        return ApplyAIResultOutcome(submission.id, False, {"status": "FAILED"})

    if submission.source == Submission.Source.OMR_SCAN:
        payload = {
            "submission_id": submission.id,
            "status": "DONE",
            "result": result or {},
            "error": None,
        }
        returned_id = apply_omr_ai_result(payload)
        submission.save(update_fields=["meta", "updated_at"])
        return ApplyAIResultOutcome(returned_id, True, {"routed": "omr_scan"})

    if submission.source == Submission.Source.HOMEWORK_VIDEO:
        r = result or {}
        filled_ratio = _coerce_float(r.get("filled_ratio"))
        too_short = _safe_bool(r.get("too_short"))
        has_content = (not too_short) and (filled_ratio >= MIN_HOMEWORK_VIDEO_FILLED_RATIO)

        meta["homework_video_result"] = {
            "has_content": has_content,
            "filled_ratio": filled_ratio,
            "too_short": too_short,
            "policy": {"min_filled_ratio": MIN_HOMEWORK_VIDEO_FILLED_RATIO},
        }
        submission.meta = meta
        submission.status = Submission.Status.DONE
        submission.error_message = ""
        submission.save(update_fields=["meta", "status", "error_message", "updated_at"])
        return ApplyAIResultOutcome(submission.id, False, {"routed": "homework_video"})

    if submission.source == Submission.Source.HOMEWORK_IMAGE:
        meta["homework_image_ocr"] = result or {}
        submission.meta = meta
        submission.status = Submission.Status.DONE
        submission.error_message = ""
        submission.save(update_fields=["meta", "status", "error_message", "updated_at"])
        return ApplyAIResultOutcome(submission.id, False, {"routed": "homework_image"})

    submission.status = Submission.Status.DONE
    submission.error_message = ""
    submission.save(update_fields=["meta", "status", "error_message", "updated_at"])
    return ApplyAIResultOutcome(submission.id, False, {"routed": "default"})
