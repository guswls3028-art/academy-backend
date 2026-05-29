"""
OMR worker dispatch payload 빌드 — Sheet/question/file_key/template_meta.

이전: dispatcher.py 의 `_build_ai_payload` 가 모든 source (OMR / HOMEWORK /
        ONLINE) 의 payload 를 한 함수에서 분기 처리했다. OMR-specific 인
        sheet_id 자료, ExamQuestion region_meta, question_count, template_meta
        가 generic dispatcher 안에 박혀 있어서 OMR 부채를 키웠다.

이 모듈은 OMR_SCAN submission 한 종류에 대한 payload 만 책임진다. 다른
source 는 dispatcher 가 직접 처리.

호출: dispatcher.py 의 _build_ai_payload 에서 source==OMR_SCAN 인 경우만.
"""
from __future__ import annotations

from typing import Any, Optional

from apps.domains.assets.omr.services.meta_generator import (
    build_objective_template_meta,
)
from apps.domains.exams.models import ExamQuestion, Sheet
from apps.domains.submissions.models import Submission
from apps.support.omr.sheet_resolver import resolve_omr_sheet_for_submission
from apps.infrastructure.storage.r2 import generate_presigned_get_url


def _safe_int(v: Any) -> Optional[int]:
    try:
        return int(v) if v is not None else None
    except Exception:
        return None


def build_omr_payload(submission: Submission) -> dict[str, Any]:
    """
    OMR_SCAN submission 에서 AI worker 가 받을 payload 를 빌드.

    Raises:
        ValueError: sheet_id 가 비정수 / 다른 시험 / 없음.
    """
    if submission.source != Submission.Source.OMR_SCAN:
        raise ValueError("build_omr_payload requires source=omr_scan")

    payload = dict(submission.payload or {})

    mode = str(payload.get("mode") or "auto").lower()
    if mode not in ("scan", "photo", "auto"):
        mode = "auto"

    raw_sheet_id = payload.get("sheet_id")
    sheet_id = _safe_int(raw_sheet_id)
    if raw_sheet_id not in (None, "") and sheet_id is None:
        raise ValueError("sheet_id must be integer")

    sheet: Sheet = resolve_omr_sheet_for_submission(submission, sheet_id)
    sheet_id = int(sheet.id)

    questions_payload: list[dict[str, Any]] = []
    qs = ExamQuestion.objects.filter(sheet_id=sheet_id).order_by("number")
    for q in qs:
        region_meta = getattr(q, "region_meta", None) or getattr(q, "meta", None)
        questions_payload.append(
            {
                "exam_question_id": int(q.id),
                "number": int(getattr(q, "number", 0) or 0),
                "region_meta": region_meta,
            }
        )

    download_url = None
    if submission.file_key:
        download_url = generate_presigned_get_url(
            key=submission.file_key, expires_in=3600
        )

    payload.update(
        {
            "submission_id": int(submission.id),
            "target_type": submission.target_type,
            "target_id": int(submission.target_id),
            "file_key": submission.file_key,
            "download_url": download_url,
            "omr": {"sheet_id": sheet_id},
            "questions": questions_payload,
            "mode": mode,
        }
    )

    question_count = int(getattr(sheet, "total_questions", 0) or 0)
    if question_count > 0:
        payload["question_count"] = question_count
        payload["template_meta"] = build_objective_template_meta(
            question_count=question_count
        )

    return payload
