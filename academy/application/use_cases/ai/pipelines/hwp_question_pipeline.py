from __future__ import annotations

from io import BytesIO
import logging
from typing import Any, Callable, Dict, Optional

from apps.infrastructure.storage.r2 import upload_fileobj_to_r2_storage
from apps.shared.contracts.ai_job import AIJob
from apps.shared.contracts.ai_result import AIResult
from academy.adapters.tools.hwp_endnote_images import (
    crop_problem_from_endnote,
    extract_document_endnotes,
)

logger = logging.getLogger(__name__)


def extract_and_upload_hwp_explanations(
    *,
    local_path: str,
    filename: str,
    tenant_id: str,
    exam_id: str | int,
) -> tuple[Any, list[dict[str, Any]]]:
    """Persist numbered teacher-authored HWP endnotes as explanation images.

    This helper is shared by the single-file HWP fallback and the preferred
    Ymath flow where a clean problem PDF is paired with a separate teacher HWP.
    The caller remains responsible for matching only numbers that exist in the
    clean problem source.
    """
    extraction = extract_document_endnotes(local_path, filename)
    if not extraction.visuals:
        raise ValueError("번호가 있는 미주 해설 이미지가 없습니다.")

    explanations: list[dict[str, Any]] = []
    for visual in extraction.visuals:
        explanation_key = (
            f"tenants/{tenant_id}/exams/explanations/"
            f"{exam_id}/q{visual.number:03d}.png"
        )
        upload_fileobj_to_r2_storage(
            fileobj=BytesIO(visual.png_bytes),
            key=explanation_key,
            content_type="image/png",
        )
        explanations.append(
            {
                "question_number": visual.number,
                "text": "",
                "page_index": max(visual.number - 1, 0),
                "image_key": explanation_key,
                "source": "source_file",
                "match_confidence": 1.0,
            }
        )
    return extraction, explanations


def merge_paired_teacher_explanations(
    *,
    primary_result: dict[str, Any],
    teacher_explanations: list[dict[str, Any]],
) -> dict[str, Any]:
    """Match a clean problem source to teacher HWP endnotes by source number."""
    result = dict(primary_result or {})
    detected_numbers = {
        int(question.get("original_number") or question.get("number"))
        for question in (result.get("questions") or [])
        if question.get("original_number") or question.get("number")
    }
    matched = [
        explanation
        for explanation in teacher_explanations
        if int(explanation.get("question_number") or 0) in detected_numbers
    ]
    result["explanations"] = matched
    result["explanation_source_mode"] = "paired_teacher_hwp"
    result["teacher_explanation_count"] = len(matched)
    result["unmatched_teacher_explanation_numbers"] = [
        int(explanation.get("question_number") or 0)
        for explanation in teacher_explanations
        if int(explanation.get("question_number") or 0) not in detected_numbers
    ]
    return result


def run_hwp_question_pipeline(
    *,
    job: AIJob,
    local_path: str,
    payload: Dict[str, Any],
    tenant_id: Optional[str],
    record_progress: Callable,
) -> AIResult:
    exam_id = payload.get("exam_id")
    filename = str(payload.get("filename") or "")
    if not tenant_id or not exam_id:
        return AIResult.failed(job.id, "tenant_id/exam_id missing")

    record_progress(
        job.id, "analyzing", 25, step_index=1, step_total=3,
        step_name_display="한글 해설 구조 분석", step_percent=0,
        tenant_id=tenant_id,
    )
    try:
        extraction = extract_document_endnotes(local_path, filename)
        if not extraction.visuals:
            raise ValueError("번호가 있는 미주 해설 이미지가 없습니다.")
    except Exception:
        logger.exception(
            "HWP_ENDNOTE_EXTRACTION_FAILED | job_id=%s | exam_id=%s",
            job.id,
            exam_id,
        )
        return AIResult.done(
            job.id,
            {
                "exam_id": exam_id,
                "conversion_required": True,
                "message": "미주 해설 이미지를 읽지 못해 PDF 변환본이 필요합니다.",
            },
        )
    if extraction.missing_visual_numbers:
        return AIResult.done(
            job.id,
            {
                "exam_id": exam_id,
                "conversion_required": True,
                "source_mode": "problem_document_requires_pdf",
                "detected_question_count": len(extraction.control_numbers),
                "extracted_visual_count": len(extraction.visuals),
                "missing_visual_numbers": list(extraction.missing_visual_numbers),
                "message": (
                    "이 한글 파일은 일부 문항만 미주 원본 이미지가 있어 문제와 해설을 "
                    "완전하게 나눌 수 없습니다. 같은 문제지를 PDF로 저장해 함께 올려 주세요."
                ),
            },
        )
    visuals = list(extraction.visuals)
    explanations: list[dict[str, Any]] = []
    for visual in visuals:
        explanation_key = (
            f"tenants/{tenant_id}/exams/explanations/"
            f"{exam_id}/q{visual.number:03d}.png"
        )
        upload_fileobj_to_r2_storage(
            fileobj=BytesIO(visual.png_bytes),
            key=explanation_key,
            content_type="image/png",
        )
        explanations.append(
            {
                "question_number": visual.number,
                "text": "",
                "page_index": max(visual.number - 1, 0),
                "image_key": explanation_key,
                "source": "source_file",
                "match_confidence": 1.0,
            }
        )
    record_progress(
        job.id, "cropping", 60, step_index=2, step_total=3,
        step_name_display="문항·원본 해설 저장", step_percent=0,
        tenant_id=tenant_id,
    )
    questions = []
    question_image_keys: dict[int, str] = {}
    for visual in visuals:
        problem_key = f"tenants/{tenant_id}/exams/questions/{exam_id}/q{visual.number:03d}.png"
        upload_fileobj_to_r2_storage(
            fileobj=BytesIO(crop_problem_from_endnote(visual.png_bytes)),
            key=problem_key,
            content_type="image/png",
        )
        question_image_keys[visual.number] = problem_key
        questions.append(
            {
                "number": visual.number,
                "original_number": visual.number,
                "bbox": [0, 0, visual.width, round(visual.height * 0.3)],
                "page_index": max(visual.number - 1, 0),
                "problem_crop_ratio": 0.3,
            }
        )

    record_progress(
        job.id, "done", 100, step_index=3, step_total=3,
        step_name_display="검수 후보 준비", step_percent=100,
        tenant_id=tenant_id,
    )
    return AIResult.done(
        job.id,
        {
            "exam_id": exam_id,
            "questions": questions,
            "explanations": explanations,
            "question_image_keys": question_image_keys,
            "total_questions": len(questions),
            "page_count": len(questions),
            "is_pdf": False,
            "source_mode": "combined_document",
            "segmentation_method": (
                "hwpx_endnote" if filename.lower().endswith(".hwpx") else "hwp_endnote"
            ),
        },
    )
