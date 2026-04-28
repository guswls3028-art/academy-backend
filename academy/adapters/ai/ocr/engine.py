from __future__ import annotations
from typing import List
from .schemas import OCRResultPayload, OMRDetectedAnswer


def run_ocr_engine(
    *,
    image_path: str,
) -> OCRResultPayload:
    """
    실제 OCR/OMR 엔진 자리
    - 지금은 더미
    - 나중에 OpenCV / Tesseract / 외부 API 교체
    """

    answers: List[OMRDetectedAnswer] = [
        {
            "question_number": 1,
            "detected": ["B"],
            "confidence": 0.92,
            "marking": "single",
            "status": "ok",
        },
        {
            "question_number": 2,
            "detected": ["D"],
            "confidence": 0.88,
            "marking": "single",
            "status": "ok",
        },
    ]

    return {
        "version": "v1",
        "answers": answers,
        "raw_text": None,
    }
