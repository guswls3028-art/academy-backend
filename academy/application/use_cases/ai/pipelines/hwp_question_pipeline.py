from __future__ import annotations

from io import BytesIO
import logging
from typing import Any, Callable, Dict, Optional

from apps.infrastructure.storage.r2 import upload_fileobj_to_r2_storage
from apps.shared.contracts.ai_job import AIJob
from apps.shared.contracts.ai_result import AIResult
from academy.adapters.tools.hwp_endnote_images import (
    crop_problem_from_endnote,
    extract_hwp_endnote_visuals,
)

logger = logging.getLogger(__name__)


def run_hwp_question_pipeline(
    *,
    job: AIJob,
    local_path: str,
    payload: Dict[str, Any],
    tenant_id: Optional[str],
    record_progress: Callable,
) -> AIResult:
    exam_id = payload.get("exam_id")
    if not tenant_id or not exam_id:
        return AIResult.failed(job.id, "tenant_id/exam_id missing")

    record_progress(
        job.id, "analyzing", 25, step_index=1, step_total=3,
        step_name_display="한글 해설 구조 분석", step_percent=0,
        tenant_id=tenant_id,
    )
    try:
        visuals = extract_hwp_endnote_visuals(local_path)
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
    if not visuals:
        return AIResult.done(
            job.id,
            {
                "exam_id": exam_id,
                "conversion_required": True,
                "message": "번호가 있는 미주 해설 이미지가 없어 PDF 변환본이 필요합니다.",
            },
        )

    record_progress(
        job.id, "cropping", 60, step_index=2, step_total=3,
        step_name_display="문항·원본 해설 저장", step_percent=0,
        tenant_id=tenant_id,
    )
    questions = []
    explanations = []
    question_image_keys: dict[int, str] = {}
    for visual in visuals:
        problem_key = f"tenants/{tenant_id}/exams/questions/{exam_id}/q{visual.number:03d}.png"
        explanation_key = f"tenants/{tenant_id}/exams/explanations/{exam_id}/q{visual.number:03d}.png"
        upload_fileobj_to_r2_storage(
            fileobj=BytesIO(crop_problem_from_endnote(visual.png_bytes)),
            key=problem_key,
            content_type="image/png",
        )
        upload_fileobj_to_r2_storage(
            fileobj=BytesIO(visual.png_bytes),
            key=explanation_key,
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
            "segmentation_method": "hwp_endnote",
        },
    )
