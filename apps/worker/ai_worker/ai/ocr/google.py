# apps/worker/ai/ocr/google.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

# google cloud vision
from google.cloud import vision  # type: ignore


@dataclass
class OCRResult:
    text: str
    confidence: Optional[float] = None
    raw: Optional[Any] = None


def google_ocr(image_path: str) -> OCRResult:
    """
    Worker에서 실행되는 Google OCR
    - service account는 GOOGLE_APPLICATION_CREDENTIALS 또는 기본 환경에 따름
    """
    client = vision.ImageAnnotatorClient()

    with open(image_path, "rb") as f:
        content = f.read()

    image = vision.Image(content=content)
    response = client.text_detection(image=image)

    if getattr(response, "error", None) and response.error.message:
        return OCRResult(text="", confidence=None, raw={"error": response.error.message})

    annotations = getattr(response, "text_annotations", None) or []
    if not annotations:
        return OCRResult(text="", confidence=None, raw=None)

    return OCRResult(
        text=annotations[0].description or "",
        confidence=None,
        raw=None,  # raw를 통째로 넘기면 직렬화 이슈가 생길 수 있어 기본 None
    )
