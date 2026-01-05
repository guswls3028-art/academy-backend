# apps/worker/ai/pipelines/dispatcher.py
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
from apps.worker.storage.downloader import download_to_tmp


def handle_ai_job(job: AIJob) -> AIResult:
    """
    Worker-side single entrypoint (STEP 2 확정판)

    규칙:
    - worker는 R2 credential 없음
    - payload["download_url"]만 신뢰
    - presigned URL → /tmp 다운로드 → local_path 사용
    """
    try:
        cfg = AIConfig.load()
        payload: Dict[str, Any] = job.payload or {}

        # 🔥 STEP 2 핵심: presigned GET URL → local file
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
                # auto: google → tesseract
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
            analysis = analyze_homework_video(
                video_path=local_path,
                frame_stride=frame_stride,
                min_frame_count=min_frame_count,
            )
            return AIResult.done(job.id, analysis)

        # --------------------------------------------------
        # OMR grading
        # --------------------------------------------------
        if job.type == "omr_grading":
            questions = payload.get("questions") or []
            from apps.worker.ai_worker.ai.omr.engine import detect_omr_answers_v1

            answers = detect_omr_answers_v1(
                image_path=local_path,
                questions=list(questions),
            )
            return AIResult.done(
                job.id,
                {
                    "version": "v1",
                    "answers": answers,
                },
            )

        return AIResult.failed(job.id, f"Unsupported job type: {job.type}")

    except Exception as e:
        return AIResult.failed(job.id, str(e))
