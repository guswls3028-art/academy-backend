from __future__ import annotations

import hashlib
import io
import logging
import os
import shutil

from apps.domains.tools.problem_review.renderers import render_problem_review_report
from apps.domains.tools.problem_review.schema import build_source_draft
from apps.domains.tools.problem_studio.async_transfer import source_files_from_archive
from apps.domains.tools.problem_studio.ocr import OcrResult, extract_ocr_text_from_image
from apps.domains.tools.problem_studio.structure import analyze_transfer_documents
from apps.domains.tools.problem_studio.transfer_documents import (
    TransferOcrContext,
    collect_transfer_documents,
)
from apps.shared.contracts.ai_job import AIJob
from apps.shared.contracts.ai_result import AIResult


logger = logging.getLogger(__name__)


def _record_progress(job: AIJob, step: str, percent: int, label: str) -> None:
    try:
        from academy.adapters.cache.redis_progress_adapter import RedisProgressAdapter

        RedisProgressAdapter().record_progress(
            job.id,
            step,
            {
                "percent": percent,
                "step_name": step,
                "step_name_display": label,
                "step_percent": percent,
            },
            tenant_id=str(job.tenant_id or ""),
        )
    except Exception:
        logger.debug("Problem review progress record skipped", exc_info=True)


def _analysis_ocr_context(job: AIJob) -> tuple[TransferOcrContext, dict[str, int | str]]:
    from academy.adapters.ai.config import AIConfig
    from academy.adapters.ai.problem.transcriber import transcribe_problem_image
    from apps.domains.ai.services.quota import consume_ai_quota

    cfg = AIConfig.load()
    try:
        configured_units = int(os.getenv("PROBLEM_REVIEW_AI_MAX_UNITS", "120"))
    except (TypeError, ValueError):
        configured_units = 120
    counters: dict[str, int | str] = {
        "ai_calls": 0,
        "fallback_calls": 0,
        "engine": "local_ocr",
    }

    def transcribe(data: bytes, mime: str) -> OcrResult:
        try:
            consume_ai_quota(kind="problem_studio_transcription")
            text = transcribe_problem_image(
                data,
                mime=mime,
                api_key=cfg.OPENAI_API_KEY or "",
                model=cfg.PROBLEM_TRANSCRIPTION_MODEL,
                bedrock_model=cfg.PROBLEM_TRANSCRIPTION_BEDROCK_MODEL,
                bedrock_region=cfg.BEDROCK_REGION,
            )
            counters["ai_calls"] = int(counters["ai_calls"]) + 1
            counters["engine"] = (
                f"openai:{cfg.PROBLEM_TRANSCRIPTION_MODEL}"
                if cfg.OPENAI_API_KEY
                else f"bedrock:{cfg.PROBLEM_TRANSCRIPTION_BEDROCK_MODEL}"
            )
            return OcrResult(
                text=text,
                status="extracted" if text else "empty",
                engine=str(counters["engine"]),
            )
        except Exception:
            logger.warning(
                "PROBLEM_REVIEW_TRANSCRIPTION_FALLBACK job_id=%s",
                job.id,
                exc_info=True,
            )
            counters["fallback_calls"] = int(counters["fallback_calls"]) + 1
            local_result = extract_ocr_text_from_image(data, mime=mime)
            return OcrResult(
                text=local_result.text,
                status=local_result.status,
                engine=local_result.engine,
                warning="AI 판독을 사용할 수 없어 로컬 OCR로 대체했습니다.",
            )

    return (
        TransferOcrContext(
            enabled=True,
            max_units=max(1, min(160, configured_units)),
            extractor=transcribe,
        ),
        counters,
    )


def handle_problem_review_analysis_job(job: AIJob) -> AIResult:
    payload = job.payload or {}
    tenant_id = str(job.tenant_id or "")
    payload_tenant_id = str(payload.get("tenant_id") or "")
    report_id = str(payload.get("report_id") or "")
    archive_key = str(payload.get("source_archive_key") or "")
    expected_prefix = f"tenants/{tenant_id}/tools/problem-review/tmp/{report_id}/"
    if not tenant_id or payload_tenant_id != tenant_id:
        return AIResult.failed(job.id, "tenant_id mismatch")
    if not report_id or not str(payload.get("request_user_id") or ""):
        return AIResult.failed(job.id, "report owner scope is missing")
    if not archive_key.startswith(expected_prefix):
        return AIResult.failed(job.id, "source archive scope mismatch")

    archive_path: str | None = None
    try:
        _record_progress(job, "source", 8, "시험지 읽는 중")
        from academy.adapters.ai.storage.downloader import download_r2_key_to_tmp

        archive_path = download_r2_key_to_tmp(
            r2_key=archive_key,
            job_id=f"{job.id}-problem-review",
        )
        ocr_context, ocr_counters = _analysis_ocr_context(job)
        with source_files_from_archive(archive_path) as source_files:
            collection = collect_transfer_documents(
                source_files=source_files,
                ocr_context=ocr_context,
            )
        structure = analyze_transfer_documents(collection.documents, collection.warnings)
        source_questions = [
            {
                "number": item.number,
                "prompt": item.prompt,
                "choices": item.choices,
                "answer": item.answer,
                "explanation": item.explanation,
                "confidence": (
                    "high" if item.confidence >= 0.85 else "medium" if item.confidence >= 0.6 else "low"
                ),
            }
            for item in structure.items
            if item.item_type == "problem"
        ][:80]
        if not source_questions:
            return AIResult.failed(
                job.id,
                "문항을 찾지 못했습니다. 더 선명한 PDF, HWPX, DOCX 또는 이미지 파일을 올려 주세요.",
            )

        metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
        warnings = list(collection.warnings)
        warnings.extend(structure.review_actions)
        if structure.structure_limit_reached:
            warnings.append("문항 수가 많아 앞의 80문항까지만 리포트 초안에 포함했습니다.")
        source_draft = build_source_draft(
            metadata=metadata,
            questions=source_questions,
            warnings=warnings,
        )
        _record_progress(job, "analysis", 45, "출제 기조와 문항별 포인트 분석 중")
        try:
            from academy.adapters.ai.problem.reviewer import generate_problem_review_report

            report = generate_problem_review_report(
                source_draft=source_draft,
                source_questions=source_questions,
            )
            engine = "ai"
        except Exception as exc:
            logger.exception("PROBLEM_REVIEW_AI_FALLBACK job_id=%s", job.id)
            source_draft["warnings"] = [
                *source_draft.get("warnings", []),
                f"AI 분석을 완료하지 못해 문항 전사 초안을 열었습니다. ({str(exc)[:160]})",
            ]
            report = source_draft
            engine = "source_draft_fallback"

        _record_progress(job, "done", 100, "검수 초안 준비 완료")
        return AIResult.done(job.id, {
            "report": report,
            "generation_engine": engine,
            "source": {
                "file_count": len(collection.input_files),
                "files": [
                    {key: item.get(key) for key in ("name", "kind", "sizeLabel")}
                    for item in collection.input_files
                ],
                "question_count": len(source_questions),
                "page_count": structure.page_count,
                "quality_level": structure.quality_level,
                "ocr_candidate_count": structure.ocr_candidate_count,
                "ocr_completed_unit_count": structure.ocr_completed_unit_count,
                "transcription_engine": ocr_counters["engine"],
                "ai_transcribed_units": ocr_counters["ai_calls"],
                "fallback_ocr_units": ocr_counters["fallback_calls"],
            },
        })
    except Exception as exc:
        logger.exception("PROBLEM_REVIEW_ANALYSIS_FAILED job_id=%s", job.id)
        return AIResult.failed(job.id, str(exc)[:2000])
    finally:
        if archive_path:
            parent = os.path.dirname(archive_path)
            if os.path.basename(parent).startswith("ai-job-"):
                shutil.rmtree(parent, ignore_errors=True)
        if archive_key.startswith(expected_prefix):
            try:
                from apps.infrastructure.storage.r2 import delete_object_r2_storage

                delete_object_r2_storage(key=archive_key)
            except Exception:
                logger.warning(
                    "PROBLEM_REVIEW_SOURCE_CLEANUP_FAILED job_id=%s",
                    job.id,
                    exc_info=True,
                )


def handle_problem_review_export_job(job: AIJob) -> AIResult:
    payload = job.payload or {}
    tenant_id = str(job.tenant_id or "")
    report_id = str(payload.get("report_id") or "")
    output_format = str(payload.get("output_format") or "").lower()
    if not tenant_id or str(payload.get("tenant_id") or "") != tenant_id:
        return AIResult.failed(job.id, "tenant_id mismatch")
    if not report_id or not str(payload.get("request_user_id") or ""):
        return AIResult.failed(job.id, "report owner scope is missing")
    if output_format not in {"pdf", "pptx"}:
        return AIResult.failed(job.id, "unsupported output format")

    try:
        _record_progress(job, "render", 35, "리포트 조판 중")
        data, filename, content_type = render_problem_review_report(
            payload.get("report") if isinstance(payload.get("report"), dict) else {},
            output_format=output_format,
        )
        key = f"tenants/{tenant_id}/tools/problem-review/{report_id}/{job.id}/{filename}"
        from apps.infrastructure.storage.r2 import upload_fileobj_to_r2_storage

        _record_progress(job, "upload", 80, "다운로드 파일 저장 중")
        upload_fileobj_to_r2_storage(
            fileobj=io.BytesIO(data),
            key=key,
            content_type=content_type,
        )
        _record_progress(job, "done", 100, "다운로드 준비 완료")
        return AIResult.done(job.id, {
            "r2_key": key,
            "filename": filename,
            "content_type": content_type,
            "output_format": output_format,
            "size_bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
            "report_version": int(payload.get("report_version") or 1),
        })
    except Exception as exc:
        logger.exception("PROBLEM_REVIEW_EXPORT_FAILED job_id=%s", job.id)
        return AIResult.failed(job.id, str(exc)[:2000])
