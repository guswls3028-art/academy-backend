# apps/worker/ai/pipelines/dispatcher.py
from __future__ import annotations

from typing import Any, Dict

from apps.shared.contracts.ai_job import AIJob
from apps.shared.contracts.ai_result import AIResult

from apps.worker.ai.config import AIConfig
from apps.worker.ai.ocr.google import google_ocr
from apps.worker.ai.ocr.tesseract import tesseract_ocr
from apps.worker.ai.detection.segment_dispatcher import segment_questions
from apps.worker.ai.handwriting.detector import analyze_handwriting
from apps.worker.ai.embedding.service import get_embeddings
from apps.worker.ai.problem.generator import generate_problem_from_ocr
from apps.worker.ai.pipelines.homework_video_analyzer import analyze_homework_video


def handle_ai_job(job: AIJob) -> AIResult:
    """
    Worker-side single entrypoint:
      AIJob -> AIResult

    - 저장하지 않음
    - 도메인 규칙 판단하지 않음
    - 필요한 계산만 수행하고 결과를 result payload로 반환
    """
    try:
        cfg = AIConfig.load()
        payload: Dict[str, Any] = job.payload or {}

        if job.type == "ocr":
            image_path = payload["image_path"]
            engine = (payload.get("engine") or cfg.OCR_ENGINE or "auto").lower()

            if engine == "tesseract":
                r = tesseract_ocr(image_path)
            elif engine == "google":
                r = google_ocr(image_path)
            else:
                # auto: google -> tesseract
                try:
                    r = google_ocr(image_path)
                    if not r.text.strip():
                        r = tesseract_ocr(image_path)
                except Exception:
                    r = tesseract_ocr(image_path)

            return AIResult.done(
                job.id,
                {"text": r.text, "confidence": r.confidence},
            )

        if job.type == "question_segmentation":
            image_path = payload["image_path"]
            boxes = segment_questions(image_path)
            return AIResult.done(job.id, {"boxes": boxes})

        if job.type == "handwriting_analysis":
            image_path = payload["image_path"]
            scores = analyze_handwriting(image_path)
            return AIResult.done(job.id, scores)

        if job.type == "embedding":
            texts = payload.get("texts") or []
            batch = get_embeddings(list(texts))
            return AIResult.done(
                job.id,
                {"backend": batch.backend, "vectors": batch.vectors},
            )

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

        if job.type == "homework_video_analysis":
            video_path = payload["video_path"]
            frame_stride = int(payload.get("frame_stride") or 10)
            min_frame_count = int(payload.get("min_frame_count") or 30)
            analysis = analyze_homework_video(
                video_path=video_path,
                frame_stride=frame_stride,
                min_frame_count=min_frame_count,
            )
            return AIResult.done(job.id, analysis)

        if job.type == "omr_grading":
            image_path = payload["image_path"]
            questions = payload.get("questions") or []
            # questions: [{question_id, roi:{x,y,w,h}, choices:[...], axis:"x"|"y"}, ...]
            from apps.worker.ai.omr.engine import detect_omr_answers_v1

            answers = detect_omr_answers_v1(
                image_path=image_path,
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
