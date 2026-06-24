from __future__ import annotations

import io
import logging
import os
import shutil
import uuid

from apps.domains.tools.problem_studio.async_transfer import source_files_from_archive
from apps.domains.tools.problem_studio.transfer_documents import build_transfer_package
from apps.shared.contracts.ai_job import AIJob
from apps.shared.contracts.ai_result import AIResult

logger = logging.getLogger(__name__)


def _record_progress(
    job_id: str,
    step: str,
    percent: int,
    *,
    tenant_id: str | None = None,
    step_index: int | None = None,
    step_total: int | None = None,
    step_name_display: str | None = None,
    step_percent: int | None = None,
) -> None:
    try:
        from academy.adapters.cache.redis_progress_adapter import RedisProgressAdapter

        extra = {"percent": percent}
        if step_index is not None and step_total is not None:
            extra.update({
                "step_index": step_index,
                "step_total": step_total,
                "step_name": step,
                "step_name_display": step_name_display or step,
                "step_percent": step_percent if step_percent is not None else percent,
            })
        RedisProgressAdapter().record_progress(job_id, step, extra, tenant_id=str(tenant_id) if tenant_id else None)
    except Exception:
        logger.debug("Problem Studio transfer progress record skipped", exc_info=True)


def handle_problem_studio_transfer_job(job: AIJob) -> AIResult:
    payload = job.payload or {}
    tenant_id = str(payload.get("tenant_id") or job.tenant_id or "")
    archive_key = str(payload.get("source_archive_key") or "")
    problem_payload = payload.get("problem_studio_payload")
    if not isinstance(problem_payload, dict):
        problem_payload = {}
    if not tenant_id:
        return AIResult.failed(job.id, "tenant_id required")
    if not archive_key:
        return AIResult.failed(job.id, "source_archive_key required")

    archive_path: str | None = None
    result_key = ""
    try:
        _record_progress(
            job.id, "downloading", 8,
            tenant_id=tenant_id, step_index=1, step_total=4,
            step_name_display="소스 준비", step_percent=0,
        )

        from academy.adapters.ai.storage.downloader import download_r2_key_to_tmp

        archive_path = download_r2_key_to_tmp(r2_key=archive_key, job_id=f"{job.id}-problem-studio")

        _record_progress(
            job.id, "processing", 18,
            tenant_id=tenant_id, step_index=2, step_total=4,
            step_name_display="원본 이관 패키지 생성", step_percent=0,
        )

        with source_files_from_archive(archive_path) as source_files:
            package = build_transfer_package(payload=problem_payload, source_files=source_files)

        _record_progress(
            job.id, "uploading", 82,
            tenant_id=tenant_id, step_index=3, step_total=4,
            step_name_display="결과 저장", step_percent=0,
        )

        from apps.infrastructure.storage.r2 import (
            generate_presigned_get_url_storage,
            upload_fileobj_to_r2_storage,
        )

        unique = uuid.uuid4().hex[:12]
        result_key = f"tenants/{tenant_id}/tools/problem-studio/{unique}/{package.filename}"
        upload_fileobj_to_r2_storage(
            fileobj=io.BytesIO(package.data),
            key=result_key,
            content_type=package.content_type,
        )
        download_url = generate_presigned_get_url_storage(
            key=result_key,
            expires_in=3600,
            filename=package.filename,
            content_type=package.content_type,
        )

        _record_progress(
            job.id, "done", 100,
            tenant_id=tenant_id, step_index=4, step_total=4,
            step_name_display="완료", step_percent=100,
        )

        logger.info(
            "PROBLEM_STUDIO_TRANSFER done job_id=%s tenant_id=%s documents=%d size=%d",
            job.id,
            tenant_id,
            len(package.documents),
            len(package.data),
        )
        return AIResult.done(job.id, {
            "download_url": download_url,
            "filename": package.filename,
            "r2_key": result_key,
            "size_bytes": len(package.data),
            "document_count": len(package.documents),
            "warning_count": len(package.warnings),
            "review_file_count": package.review_file_count,
            "structured_item_count": package.structured_item_count,
            "ocr_candidate_count": package.ocr_candidate_count,
            "quality_level": package.quality_level,
        })
    except Exception as exc:
        logger.exception("PROBLEM_STUDIO_TRANSFER failed job_id=%s tenant_id=%s", job.id, tenant_id)
        return AIResult.failed(job.id, str(exc)[:2000])
    finally:
        if archive_path:
            parent = os.path.dirname(archive_path)
            if os.path.basename(parent).startswith("ai-job-"):
                shutil.rmtree(parent, ignore_errors=True)
