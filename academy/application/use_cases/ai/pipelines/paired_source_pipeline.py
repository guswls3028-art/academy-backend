"""Deterministic problem/answer/explanation source pairing for exam uploads."""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, Optional

from apps.shared.contracts.ai_job import AIJob
from apps.shared.contracts.ai_result import AIResult
from academy.adapters.ai.storage.downloader import cleanup_tmp_for_path, download_to_tmp
from academy.application.use_cases.ai.pipelines.pdf_question_pipeline import (
    run_pdf_question_pipeline,
)


logger = logging.getLogger(__name__)
_IMAGE_OR_PDF_SUFFIXES = (".pdf", ".png", ".jpg", ".jpeg")


def attach_paired_source_results(
    *,
    job: AIJob,
    primary_result: AIResult,
    payload: Dict[str, Any],
    tenant_id: Optional[str],
    record_progress: Callable,
) -> AIResult:
    """Attach source facts by exact original question number.

    Teacher text is copied from deterministic extractors only. Missing,
    unmatched, unsupported, or failed sources remain explicit review issues;
    this boundary never asks a generative model to fill or rewrite content.
    """
    result_payload = dict(primary_result.result or {})
    detected_numbers = {
        int(question.get("original_number") or question.get("number"))
        for question in (result_payload.get("questions") or [])
        if question.get("original_number") or question.get("number")
    }
    source_issues: list[str] = []
    answer_requested = bool(payload.get("answer_source_requested"))
    explanation_requested = bool(payload.get("explanation_source_requested"))

    explanation_download_url = str(
        payload.get("explanation_download_url") or ""
    ).strip()
    explanation_filename = str(payload.get("explanation_filename") or "").lower()
    teacher_explanations: list[dict[str, Any]] = []
    explanation_local_path = ""
    if explanation_download_url:
        try:
            explanation_local_path = download_to_tmp(
                download_url=explanation_download_url,
                job_id=f"{job.id}-teacher-explanation",
            )
            if explanation_filename.endswith((".hwp", ".hwpx")):
                from academy.application.use_cases.ai.pipelines.hwp_question_pipeline import (
                    extract_and_upload_hwp_explanations,
                )

                _, teacher_explanations = extract_and_upload_hwp_explanations(
                    local_path=explanation_local_path,
                    filename=explanation_filename,
                    tenant_id=str(tenant_id),
                    exam_id=payload["exam_id"],
                )
                result_payload["explanation_source_mode"] = "paired_teacher_hwp"
            else:
                explanation_result = run_pdf_question_pipeline(
                    job=job,
                    local_path=explanation_local_path,
                    payload={**payload, "source_role": "explanation"},
                    tenant_id=tenant_id,
                    record_progress=record_progress,
                )
                teacher_explanations = list(
                    (explanation_result.result or {}).get("explanations") or []
                )
                source_issues.extend(
                    (explanation_result.result or {}).get("source_issues") or []
                )
                result_payload["explanation_source_mode"] = (
                    "paired_source_document"
                )
        except Exception:
            logger.exception(
                "PAIRED_EXPLANATION_EXTRACTION_FAILED | job_id=%s",
                job.id,
            )
            source_issues.append("explanation_source_processing_failed")
        finally:
            if explanation_local_path:
                cleanup_tmp_for_path(explanation_local_path)
    elif explanation_requested:
        source_issues.append("explanation_source_preserved_manual_review")

    matched_explanations = [
        explanation
        for explanation in teacher_explanations
        if int(explanation.get("question_number") or 0) in detected_numbers
    ]
    if explanation_requested or explanation_download_url:
        result_payload["explanations"] = matched_explanations
    else:
        # The problem source may contain its own trusted explanation tail.
        matched_explanations = list(result_payload.get("explanations") or [])
    result_payload["teacher_explanation_count"] = len(matched_explanations)
    result_payload["unmatched_teacher_explanation_numbers"] = [
        int(explanation.get("question_number") or 0)
        for explanation in teacher_explanations
        if int(explanation.get("question_number") or 0) not in detected_numbers
    ]

    answer_download_url = str(payload.get("answer_download_url") or "").strip()
    answer_filename = str(payload.get("answer_filename") or "").lower()
    answers: list[dict[str, Any]] = []
    answer_local_path = ""
    if answer_download_url:
        try:
            answer_local_path = download_to_tmp(
                download_url=answer_download_url,
                job_id=f"{job.id}-answer-source",
            )
            if answer_filename.endswith(_IMAGE_OR_PDF_SUFFIXES):
                answer_result = run_pdf_question_pipeline(
                    job=job,
                    local_path=answer_local_path,
                    payload={**payload, "source_role": "answer"},
                    tenant_id=tenant_id,
                    record_progress=record_progress,
                )
                answers = list((answer_result.result or {}).get("answers") or [])
                source_issues.extend(
                    (answer_result.result or {}).get("source_issues") or []
                )
                result_payload["answer_source_mode"] = "paired_source_document"
            else:
                source_issues.append("answer_source_preserved_manual_review")
        except Exception:
            logger.exception(
                "PAIRED_ANSWER_EXTRACTION_FAILED | job_id=%s",
                job.id,
            )
            source_issues.append("answer_source_processing_failed")
        finally:
            if answer_local_path:
                cleanup_tmp_for_path(answer_local_path)
    elif answer_requested:
        source_issues.append("answer_source_preserved_manual_review")

    matched_answers = [
        answer
        for answer in answers
        if int(answer.get("question_number") or 0) in detected_numbers
    ]
    result_payload["answers"] = matched_answers
    result_payload["answer_count"] = len(matched_answers)
    result_payload["unmatched_answer_numbers"] = [
        int(answer.get("question_number") or 0)
        for answer in answers
        if int(answer.get("question_number") or 0) not in detected_numbers
    ]
    result_payload["answer_source_requested"] = answer_requested
    result_payload["explanation_source_requested"] = explanation_requested
    matched_answer_numbers = {
        int(answer.get("question_number") or 0)
        for answer in matched_answers
    }
    matched_explanation_numbers = {
        int(explanation.get("question_number") or 0)
        for explanation in matched_explanations
    }
    result_payload["missing_answer_numbers"] = (
        sorted(detected_numbers - matched_answer_numbers)
        if answer_requested
        else []
    )
    result_payload["missing_explanation_numbers"] = (
        sorted(detected_numbers - matched_explanation_numbers)
        if explanation_requested
        else []
    )
    if result_payload["missing_answer_numbers"]:
        source_issues.append("answer_coverage_incomplete")
    if result_payload["missing_explanation_numbers"]:
        source_issues.append("explanation_coverage_incomplete")
    result_payload["source_issues"] = list(dict.fromkeys(source_issues))
    result_payload["paired_source_status"] = (
        "partial" if result_payload["source_issues"] else "complete"
    )
    return AIResult.done(job.id, result_payload)
