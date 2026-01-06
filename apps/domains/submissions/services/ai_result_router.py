# PATH: apps/domains/submissions/services/ai_result_router.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple
import logging

from django.db import transaction

from apps.domains.submissions.models import Submission
from apps.domains.submissions.services.ai_omr_result_mapper import apply_omr_ai_result

logger = logging.getLogger(__name__)


# =========================================================
# STEP 1 정책 상수 (운영하면서 조정)
# =========================================================

MIN_HOMEWORK_VIDEO_FILLED_RATIO = 0.10  # 이 이상이면 "작성 있음"으로 판정


@dataclass(frozen=True)
class ApplyAIResultOutcome:
    """
    API 내부 콜백에서 쓰는 결과
    - should_grade: True면 results 채점 Celery를 enqueue 해야 함 (시험 제출)
    - returned_submission_id: 처리된 submission id (없으면 None)
    """
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
    """
    STEP 1 단일 진실:
    - AI result 콜백은 submission.source 기준으로 라우팅한다.
    - worker가 job.type을 같이 안 보내도 됨.
    """
    try:
        submission = Submission.objects.select_for_update().get(id=int(submission_id))
    except Submission.DoesNotExist:
        return ApplyAIResultOutcome(
            returned_submission_id=None,
            should_grade=False,
            detail={"error": "submission not found"},
        )

    # ---------------------------------------------
    # 공통: ai_result 원본은 submission.meta에 저장
    # (payload는 요청 입력이므로 덜 오염시키는 게 좋다)
    # ---------------------------------------------
    meta = dict(submission.meta or {})
    meta["ai_result"] = {
        "status": status,
        "result": result,
        "error": error,
    }
    submission.meta = meta

    # FAILED 처리
    if str(status).upper() == "FAILED":
        submission.status = Submission.Status.FAILED
        submission.error_message = error or "AI worker failed"
        submission.save(update_fields=["meta", "status", "error_message", "updated_at"])
        return ApplyAIResultOutcome(
            returned_submission_id=submission.id,
            should_grade=False,
            detail={"status": "FAILED"},
        )

    # ---------------------------------------------
    # 라우팅: submission.source 기준
    # ---------------------------------------------

    # 1) 시험 OMR: answers 저장 → ANSWERS_READY → 채점 enqueue
    if submission.source == Submission.Source.OMR_SCAN:
        # 기존 mapper는 payload 구조를 기대하므로 최소 형태로 맞춰 준다.
        # (네 기존 submit_ai_result_view가 payload를 섞는 방식과 충돌 방지)
        payload_for_mapper = {
            "submission_id": submission.id,
            "status": "DONE",
            "result": result or {},
            "error": None,
        }
        returned_id = apply_omr_ai_result(payload_for_mapper)

        # mapper가 status를 ANSWERS_READY로 만들어줌
        submission.save(update_fields=["meta", "updated_at"])

        return ApplyAIResultOutcome(
            returned_submission_id=returned_id,
            should_grade=True,  # ✅ 시험은 채점해야 함
            detail={"routed": "omr_scan"},
        )

    # 2) 영상 숙제 분석: "작성 있음/없음" 판별을 meta에 고정 후 DONE 처리
    if submission.source == Submission.Source.HOMEWORK_VIDEO:
        r = result or {}
        filled_ratio = _coerce_float(r.get("filled_ratio"), 0.0)
        too_short = _safe_bool(r.get("too_short"), False)

        # 판정 규칙 (STEP 1 고정)
        has_content = (not too_short) and (filled_ratio >= MIN_HOMEWORK_VIDEO_FILLED_RATIO)

        meta = dict(submission.meta or {})
        meta["homework_video_result"] = {
            "has_content": bool(has_content),
            "filled_ratio": float(filled_ratio),
            "too_short": bool(too_short),
            "policy": {
                "min_filled_ratio": MIN_HOMEWORK_VIDEO_FILLED_RATIO,
            },
        }
        submission.meta = meta

        # 영상 숙제는 "채점"이 아니라 "판별 결과 확정"이므로 DONE 처리
        submission.status = Submission.Status.DONE
        submission.error_message = ""
        submission.save(update_fields=["meta", "status", "error_message", "updated_at"])

        return ApplyAIResultOutcome(
            returned_submission_id=submission.id,
            should_grade=False,
            detail={"routed": "homework_video", "has_content": has_content},
        )

    # 3) 이미지 OCR 숙제(있다면): 결과만 저장하고 DONE 처리 (채점 X)
    if submission.source == Submission.Source.HOMEWORK_IMAGE:
        meta = dict(submission.meta or {})
        meta["homework_image_ocr"] = result or {}
        submission.meta = meta

        submission.status = Submission.Status.DONE
        submission.error_message = ""
        submission.save(update_fields=["meta", "status", "error_message", "updated_at"])

        return ApplyAIResultOutcome(
            returned_submission_id=submission.id,
            should_grade=False,
            detail={"routed": "homework_image"},
        )

    # 그 외: 일단 meta 저장 + DONE (안전)
    submission.status = Submission.Status.DONE
    submission.error_message = ""
    submission.save(update_fields=["meta", "status", "error_message", "updated_at"])

    return ApplyAIResultOutcome(
        returned_submission_id=submission.id,
        should_grade=False,
        detail={"routed": "default_done"},
    )
