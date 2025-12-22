# apps/worker/ai/ocr/tesseract.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from PIL import Image  # type: ignore
import pytesseract  # type: ignore


@dataclass
class OCRResult:
    text: str
    confidence: Optional[float] = None
    raw: Optional[Any] = None


def tesseract_ocr(image_path: str) -> OCRResult:
    img = Image.open(image_path)

    data = pytesseract.image_to_data(img, output_type=pytesseract.Output.DICT)
    text = "\n".join(data.get("text", [])).strip()

    confs = [c for c in data.get("conf", []) if c != -1]
    confidence = (sum(confs) / len(confs)) if confs else None

    # raw=data 는 너무 클 수 있어 필요 시만 켜기
    return OCRResult(text=text, confidence=confidence, raw=None)
