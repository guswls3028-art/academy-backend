# PATH: apps/worker/ai_worker/ai/pipelines/ppt_handler.py
# PPT generation worker handler — follows excel_handler.py pattern exactly.
#
# Modes:
#   - "images": download pre-uploaded images → assemble PPT
#   - "pdf": download PDF → question splitting → PPT

from __future__ import annotations

import io
import logging
import os
import shutil
import tempfile
import uuid

from apps.shared.contracts.ai_result import AIResult
from apps.shared.contracts.ai_job import AIJob

logger = logging.getLogger(__name__)

PPT_STEP_TOTAL = 4
PPT_STEPS = {
    "downloading": "파일 다운로드",
    "processing": "PPT 생성 중",
    "uploading": "파일 저장",
    "done": "완료",
}


def _record_progress(
    job_id: str,
    step: str,
    percent: int,
    step_index: int | None = None,
    step_total: int | None = None,
    step_name_display: str | None = None,
    step_percent: int | None = None,
    tenant_id: str | None = None,
) -> None:
    """Redis progress recording for real-time progress bar display."""
    try:
        from academy.adapters.cache.redis_progress_adapter import RedisProgressAdapter
        extra = {"percent": percent}
        if step_index is not None and step_total is not None:
            extra.update({
                "step_index": step_index,
                "step_total": step_total,
                "step_name": step,
                "step_name_display": step_name_display or PPT_STEPS.get(step, step),
                "step_percent": step_percent if step_percent is not None else 100,
            })
        tenant_id_str = str(tenant_id) if tenant_id else None
        RedisProgressAdapter().record_progress(job_id, step, extra, tenant_id=tenant_id_str)
    except Exception as e:
        logger.debug("Redis progress record skip: %s", e)


def handle_ppt_generation_job(job: AIJob) -> AIResult:
    """PPT generation handler — dispatched from dispatcher.py.

    Payload fields:
      - mode: "images" or "pdf" (default: "images")
      - r2_keys: list of R2 object keys for images (mode=images)
      - r2_key: R2 object key for PDF (mode=pdf)
      - config: {aspect_ratio, background, fit_mode}
      - tenant_id: tenant ID for R2 storage path
      - settings: image processing settings (invert, grayscale, etc.)
    """
    payload = job.payload or {}
    tenant_id = str(payload.get("tenant_id") or job.tenant_id or "") if (
        payload.get("tenant_id") or job.tenant_id
    ) else None

    mode = str(payload.get("mode", "images")).lower()
    config = payload.get("config") or {}
    settings = payload.get("settings") or {}

    tmp_dirs: list[str] = []
    unique_id = uuid.uuid4().hex[:12]

    try:
        # ──────────────── Step 1: Download ────────────────
        _record_progress(
            job.id, "downloading", 10,
            step_index=1, step_total=PPT_STEP_TOTAL,
            step_name_display="파일 다운로드", step_percent=0,
            tenant_id=tenant_id,
        )

        if mode == "pdf":
            r2_key = payload.get("r2_key")
            if not r2_key:
                return AIResult.failed(job.id, "r2_key required for pdf mode")

            from apps.worker.ai_worker.storage.downloader import download_r2_key_to_tmp
            pdf_path = download_r2_key_to_tmp(r2_key=r2_key, job_id=str(job.id))
            tmp_dirs.append(os.path.dirname(pdf_path))

            _record_progress(
                job.id, "downloading", 15,
                step_index=1, step_total=PPT_STEP_TOTAL,
                step_name_display="파일 다운로드", step_percent=100,
                tenant_id=tenant_id,
            )

            # ──────────────── Step 2: Process PDF ────────────────
            _record_progress(
                job.id, "processing", 20,
                step_index=2, step_total=PPT_STEP_TOTAL,
                step_name_display="문항 분리 중", step_percent=0,
                tenant_id=tenant_id,
            )

            from academy.application.use_cases.tools.generate_ppt import GeneratePptFromPdfUseCase

            def _on_pdf_progress(pct: int, step_name: str) -> None:
                overall = 20 + int(pct * 0.55)  # 20% to 75%
                _record_progress(
                    job.id, "processing", overall,
                    step_index=2, step_total=PPT_STEP_TOTAL,
                    step_name_display=step_name, step_percent=pct,
                    tenant_id=tenant_id,
                )

            result = GeneratePptFromPdfUseCase().execute(
                pdf_path, config=config, on_progress=_on_pdf_progress,
                image_settings=settings,
            )
            pptx_bytes = result.pptx_bytes
            slide_count = result.slide_count

        else:
            # mode == "images"
            r2_keys = payload.get("r2_keys") or []
            if not r2_keys:
                return AIResult.failed(job.id, "r2_keys required for images mode")

            from apps.worker.ai_worker.storage.downloader import download_r2_key_to_tmp

            image_bytes_list = []
            total_imgs = len(r2_keys)
            for i, key in enumerate(r2_keys):
                local_path = download_r2_key_to_tmp(r2_key=key, job_id=f"{job.id}-{i}")
                tmp_dirs.append(os.path.dirname(local_path))
                with open(local_path, "rb") as f:
                    raw_bytes = f.read()

                # Apply image processing if settings provided
                processed = _apply_image_settings(raw_bytes, settings, i)
                image_bytes_list.append(processed)

                dl_pct = int((i + 1) / total_imgs * 100)
                _record_progress(
                    job.id, "downloading", 10 + int(dl_pct * 0.1),
                    step_index=1, step_total=PPT_STEP_TOTAL,
                    step_name_display=f"다운로드 {i + 1}/{total_imgs}",
                    step_percent=dl_pct,
                    tenant_id=tenant_id,
                )

            # ──────────────── Step 2: Generate PPT ────────────────
            _record_progress(
                job.id, "processing", 25,
                step_index=2, step_total=PPT_STEP_TOTAL,
                step_name_display="PPT 생성 중", step_percent=0,
                tenant_id=tenant_id,
            )

            from academy.application.use_cases.tools.generate_ppt import GeneratePptUseCase

            def _on_img_progress(pct: int, step_name: str) -> None:
                overall = 25 + int(pct * 0.50)  # 25% to 75%
                _record_progress(
                    job.id, "processing", overall,
                    step_index=2, step_total=PPT_STEP_TOTAL,
                    step_name_display=step_name, step_percent=pct,
                    tenant_id=tenant_id,
                )

            result = GeneratePptUseCase().execute(
                image_bytes_list, config=config, on_progress=_on_img_progress,
            )
            pptx_bytes = result.pptx_bytes
            slide_count = result.slide_count

        # ──────────────── Step 3: Upload to R2 ────────────────
        _record_progress(
            job.id, "uploading", 80,
            step_index=3, step_total=PPT_STEP_TOTAL,
            step_name_display="파일 저장", step_percent=0,
            tenant_id=tenant_id,
        )

        r2_key = f"tenants/{tenant_id}/tools/ppt/{unique_id}.pptx"
        filename = f"presentation_{unique_id}.pptx"
        content_type = "application/vnd.openxmlformats-officedocument.presentationml.presentation"

        from apps.infrastructure.storage.r2 import (
            upload_fileobj_to_r2_storage,
            generate_presigned_get_url_storage,
        )

        upload_fileobj_to_r2_storage(
            fileobj=io.BytesIO(pptx_bytes),
            key=r2_key,
            content_type=content_type,
        )
        download_url = generate_presigned_get_url_storage(
            key=r2_key,
            expires_in=3600,
            filename=filename,
            content_type=content_type,
        )

        _record_progress(
            job.id, "uploading", 95,
            step_index=3, step_total=PPT_STEP_TOTAL,
            step_name_display="파일 저장", step_percent=100,
            tenant_id=tenant_id,
        )

        # ──────────────── Step 4: Done ────────────────
        _record_progress(
            job.id, "done", 100,
            step_index=4, step_total=PPT_STEP_TOTAL,
            step_name_display="완료", step_percent=100,
            tenant_id=tenant_id,
        )

        logger.info(
            "PPT_GENERATION done job_id=%s tenant_id=%s mode=%s slides=%d size=%d",
            job.id, tenant_id, mode, slide_count, len(pptx_bytes),
        )

        return AIResult.done(job.id, {
            "download_url": download_url,
            "filename": filename,
            "slide_count": slide_count,
            "size_bytes": len(pptx_bytes),
        })

    except Exception as e:
        logger.exception(
            "PPT_GENERATION failed job_id=%s tenant_id=%s mode=%s: %s",
            job.id, tenant_id, mode, e,
        )
        return AIResult.failed(job.id, str(e)[:2000])

    finally:
        # Cleanup all temp directories
        for tmp_dir in tmp_dirs:
            try:
                if os.path.isdir(tmp_dir):
                    shutil.rmtree(tmp_dir, ignore_errors=True)
            except Exception:
                pass


def _apply_image_settings(
    raw_bytes: bytes,
    settings: dict,
    slide_index: int,
) -> bytes:
    """Apply image processing settings (invert, grayscale, etc.).

    Uses the existing services.py _process_image function for consistency.
    """
    # Extract per-slide or global settings
    per_slide = settings.get("per_slide")
    if isinstance(per_slide, list) and slide_index < len(per_slide):
        ss = per_slide[slide_index]
        if not isinstance(ss, dict):
            ss = {}
    else:
        ss = {}

    invert = ss.get("invert", settings.get("invert", False))
    grayscale = ss.get("grayscale", settings.get("grayscale", False))
    auto_enhance = ss.get("auto_enhance", settings.get("auto_enhance", False))
    brightness = float(ss.get("brightness", settings.get("brightness", 1.0)))
    contrast = float(ss.get("contrast", settings.get("contrast", 1.0)))

    # No effects needed: return original bytes (preserve quality)
    if not any([invert, grayscale, auto_enhance]) and brightness == 1.0 and contrast == 1.0:
        return raw_bytes

    # Apply effects using existing service logic
    from apps.domains.tools.ppt.services import _process_image
    return _process_image(
        raw_bytes,
        invert=invert,
        grayscale=grayscale,
        auto_enhance=auto_enhance,
        brightness=brightness,
        contrast=contrast,
    )
