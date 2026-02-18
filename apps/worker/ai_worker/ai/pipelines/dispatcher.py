# apps/worker/ai_worker/ai/pipelines/dispatcher.py
from __future__ import annotations

from typing import Any, Dict

from apps.shared.contracts.ai_job import AIJob
from apps.shared.contracts.ai_result import AIResult

from apps.worker.ai_worker.ai.config import AIConfig
from apps.worker.ai_worker.ai.ocr.google import google_ocr
from apps.worker.ai_worker.ai.ocr.tesseract import tesseract_ocr
from apps.worker.ai_worker.ai.detection.segment_dispatcher import segment_questions
from apps.worker.ai_worker.ai.handwriting.detector import analyze_handwriting
from apps.worker.ai_worker.ai.embedding.service import get_embeddings
from apps.worker.ai_worker.ai.problem.generator import generate_problem_from_ocr
from apps.worker.ai_worker.ai.pipelines.homework_video_analyzer import analyze_homework_video
from apps.worker.ai_worker.ai.pipelines.excel_handler import handle_excel_parsing_job
from apps.worker.ai_worker.ai.pipelines.excel_export_handler import (
    handle_attendance_excel_export,
    handle_staff_excel_export,
)
from apps.worker.ai_worker.ai.utils.image_resizer import resize_if_large
from apps.worker.ai_worker.storage.downloader import download_to_tmp
import logging

logger = logging.getLogger(__name__)

# 구간별 진행률 기록 함수 (공통)
def _record_progress(
    job_id: str,
    step: str,
    percent: int,
    step_index: int | None = None,
    step_total: int | None = None,
    step_name_display: str | None = None,
    step_percent: int | None = None,
) -> None:
    """Redis 진행률 기록 (우하단 실시간 프로그래스바용). 구간별 진행률 지원."""
    try:
        from src.infrastructure.cache.redis_progress_adapter import RedisProgressAdapter
        extra = {"percent": percent}
        if step_index is not None and step_total is not None:
            extra.update({
                "step_index": step_index,
                "step_total": step_total,
                "step_name": step,
                "step_name_display": step_name_display or step,
                "step_percent": step_percent if step_percent is not None else 100,
            })
        RedisProgressAdapter().record_progress(job_id, step, extra)
    except Exception as e:
        logger.debug("Redis progress record skip: %s", e)


def handle_ai_job(job: AIJob) -> AIResult:
    try:
        # 작업 분기: type / task / job_type 으로 EXCEL_PARSING vs AI 작업 분리
        job_type_lower = (job.type or "").strip().lower()
        if job_type_lower == "excel_parsing":
            return handle_excel_parsing_job(job)
        if job_type_lower == "attendance_excel_export":
            return handle_attendance_excel_export(job)
        if job_type_lower == "staff_excel_export":
            return handle_staff_excel_export(job)

        cfg = AIConfig.load()
        payload: Dict[str, Any] = job.payload or {}

        download_url = payload.get("download_url")
        if not download_url:
            return AIResult.failed(job.id, "download_url missing")

        local_path = download_to_tmp(
            download_url=download_url,
            job_id=str(job.id),
        )

        # --------------------------------------------------
        # OCR
        # --------------------------------------------------
        if job.type == "ocr":
            engine = (payload.get("engine") or cfg.OCR_ENGINE or "auto").lower()

            if engine == "tesseract":
                r = tesseract_ocr(local_path)
            elif engine == "google":
                r = google_ocr(local_path)
            else:
                try:
                    r = google_ocr(local_path)
                    if not r.text.strip():
                        r = tesseract_ocr(local_path)
                except Exception:
                    r = tesseract_ocr(local_path)

            return AIResult.done(
                job.id,
                {"text": r.text, "confidence": r.confidence},
            )

        # --------------------------------------------------
        # Question segmentation
        # --------------------------------------------------
        if job.type == "question_segmentation":
            boxes = segment_questions(local_path)
            return AIResult.done(job.id, {"boxes": boxes})

        # --------------------------------------------------
        # Handwriting analysis
        # --------------------------------------------------
        if job.type == "handwriting_analysis":
            scores = analyze_handwriting(local_path)
            return AIResult.done(job.id, scores)

        # --------------------------------------------------
        # Embedding
        # --------------------------------------------------
        if job.type == "embedding":
            texts = payload.get("texts") or []
            batch = get_embeddings(list(texts))
            return AIResult.done(
                job.id,
                {"backend": batch.backend, "vectors": batch.vectors},
            )

        # --------------------------------------------------
        # Problem generation
        # --------------------------------------------------
        if job.type == "problem_generation":
            ocr_text = payload.get("ocr_text") or ""
            parsed = generate_problem_from_ocr(ocr_text)
            return AIResult.done(
                job.id,
                {
                    "body": parsed.body,
                    "choices": parsed.choices,
                    "answer": parsed.answer,
                    "difficulty": parsed.difficulty,
                    "tag": parsed.tag,
                    "summary": parsed.summary,
                    "explanation": parsed.explanation,
                },
            )

        # --------------------------------------------------
        # Homework video analysis
        # --------------------------------------------------
        if job.type == "homework_video_analysis":
            frame_stride = int(payload.get("frame_stride") or 10)
            min_frame_count = int(payload.get("min_frame_count") or 30)
            use_key_frames = payload.get("use_key_frames", True)  # 기본값: 키 프레임 사용
            max_pages = int(payload.get("max_pages") or 10)
            processing_timeout = int(payload.get("processing_timeout") or 60)
            
            analysis = analyze_homework_video(
                video_path=local_path,
                frame_stride=frame_stride,
                min_frame_count=min_frame_count,
                use_key_frames=use_key_frames,
                max_pages=max_pages,
                processing_timeout=processing_timeout,
            )
            return AIResult.done(job.id, analysis)

        # --------------------------------------------------
        # OMR grading (meta-aware) - production final
        # --------------------------------------------------
        if job.type == "omr_grading":
            """
            payload options (recommended):
              - mode: "scan" | "photo" | "auto" (default auto)
              - question_count: 10|20|30 (required if template_fetch is used)
              - template_meta: dict (inject meta directly; no API call)
              - template_fetch:
                    {
                      "base_url": "...",
                      "cookie": "...",
                      "bearer_token": "...",
                      "worker_token": "...",
                      "timeout": 10
                    }

            Output contract:
              {
                "version": "v1",
                "mode": "...",
                "aligned": true|false,
                "identifier": {...},
                "answers": [...],
                "meta_used": true|false
              }
            """
            import cv2  # type: ignore

            from apps.worker.ai_worker.ai.omr.engine import detect_omr_answers_v1, OMRConfigV1
            from apps.worker.ai_worker.ai.omr.roi_builder import build_questions_payload_from_meta
            from apps.worker.ai_worker.ai.omr.warp import warp_to_a4_landscape
            from apps.worker.ai_worker.ai.omr.template_meta import fetch_objective_meta, TemplateMetaFetchError
            from apps.worker.ai_worker.ai.omr.identifier import detect_identifier_v1, IdentifierConfigV1

            mode = str(payload.get("mode") or "auto").lower()
            if mode not in ("scan", "photo", "auto"):
                mode = "auto"

            # 1) meta 확보
            meta = payload.get("template_meta")
            meta_used = False
            meta_fetch_error = None

            if not meta:
                tf = payload.get("template_fetch") or {}
                base_url = tf.get("base_url")
                if base_url:
                    qc = int(payload.get("question_count") or 0)
                    if qc not in (10, 20, 30):
                        return AIResult.failed(job.id, "question_count required (10|20|30) for template_fetch")

                    try:
                        meta_obj = fetch_objective_meta(
                            base_url=str(base_url),
                            question_count=qc,
                            auth_cookie_header=tf.get("cookie"),
                            bearer_token=tf.get("bearer_token"),
                            worker_token_header=tf.get("worker_token"),
                            timeout=int(tf.get("timeout") or 10),
                        )
                        meta = meta_obj.raw
                        meta_used = True
                    except TemplateMetaFetchError as e:
                        meta = None
                        meta_fetch_error = str(e)[:500]

            # 2) 이미지 로드 및 리사이징
            img_bgr = cv2.imread(local_path)
            if img_bgr is None:
                return AIResult.failed(job.id, "cannot read image")
            
            # 대용량 이미지 리사이징 (처리 전)
            img_bgr, was_resized = resize_if_large(img_bgr, max_megapixels=4.0)

            aligned = img_bgr

            # 3) mode 정책
            if mode == "photo":
                warped = warp_to_a4_landscape(img_bgr)
                if warped is None:
                    return AIResult.failed(job.id, "warp_failed_for_photo_mode")
                aligned = warped

            elif mode == "auto":
                warped = warp_to_a4_landscape(img_bgr)
                if warped is not None:
                    aligned = warped

            # 4) meta 없으면 legacy
            if not meta:
                questions = payload.get("questions") or []
                if not questions:
                    return AIResult.failed(job.id, "template_meta/template_fetch failed and legacy questions missing")

                answers = detect_omr_answers_v1(
                    image_path=local_path,
                    questions=list(questions),
                    cfg=None,
                )
                return AIResult.done(
                    job.id,
                    {
                        "version": "v1",
                        "mode": "legacy_questions",
                        "aligned": False,
                        "identifier": None,
                        "answers": answers,
                        "meta_used": False,
                        "debug": {
                            "meta_fetch_error": meta_fetch_error,
                        },
                    },
                )

            # 5) ROI + identifier
            h, w = aligned.shape[:2]
            questions_payload = build_questions_payload_from_meta(meta, (w, h))

            ident = detect_identifier_v1(
                image_bgr=aligned,
                meta=meta,
                cfg=IdentifierConfigV1(),
            )

            # 6) aligned 저장 후 OMR
            import tempfile, os

            tmp_path = os.path.join(tempfile.gettempdir(), f"omr_aligned_{job.id}.jpg")
            cv2.imwrite(tmp_path, aligned)

            cfg = OMRConfigV1()
            answers = detect_omr_answers_v1(
                image_path=tmp_path,
                questions=list(questions_payload),
                cfg=cfg,
            )

            return AIResult.done(
                job.id,
                {
                    "version": "v1",
                    "mode": mode,
                    "aligned": bool(aligned is not img_bgr),
                    "identifier": ident,
                    "answers": answers,
                    "meta_used": meta_used,
                    "debug": {
                        "meta_fetch_error": meta_fetch_error,
                    },
                },
            )

        return AIResult.failed(job.id, f"Unsupported job type: {job.type}")

    except Exception as e:
        return AIResult.failed(job.id, str(e))
