from __future__ import annotations

import io
import os
from dataclasses import dataclass

from apps.domains.tools.problem_studio.structure import normalize_space


DEFAULT_OCR_TIMEOUT_SECONDS = 12
DEFAULT_OCR_MAX_UNITS = 8


@dataclass(frozen=True)
class OcrResult:
    text: str
    status: str
    engine: str = "tesseract"
    warning: str = ""


def problem_studio_ocr_enabled() -> bool:
    value = os.getenv("PROBLEM_STUDIO_OCR_ENABLED", "1").strip().lower()
    return value not in {"0", "false", "no", "off"}


def problem_studio_ocr_max_units() -> int:
    raw = os.getenv("PROBLEM_STUDIO_OCR_MAX_UNITS", str(DEFAULT_OCR_MAX_UNITS)).strip()
    try:
        return max(0, int(raw))
    except ValueError:
        return DEFAULT_OCR_MAX_UNITS


def _ocr_lang_candidates() -> list[str]:
    configured = os.getenv("PROBLEM_STUDIO_OCR_LANG", "kor+eng").strip() or "kor+eng"
    candidates = [configured]
    if configured != "eng":
        candidates.append("eng")
    return candidates


def extract_ocr_text_from_image(data: bytes, *, mime: str | None = None) -> OcrResult:
    """Run bounded local OCR for a raster page/image.

    The transfer endpoint treats failures as a queue fallback, so this helper
    never raises for missing binaries, missing language packs, or unreadable
    image bytes.
    """
    if not problem_studio_ocr_enabled():
        return OcrResult(text="", status="disabled", warning="OCR 비활성화")

    try:
        from PIL import Image
        import pytesseract
    except Exception:
        return OcrResult(text="", status="unavailable", warning="OCR 엔진을 사용할 수 없음")

    try:
        with Image.open(io.BytesIO(data)) as image:
            if image.mode not in {"RGB", "L"}:
                image = image.convert("RGB")
            last_error = ""
            for lang in _ocr_lang_candidates():
                try:
                    text = pytesseract.image_to_string(
                        image,
                        lang=lang,
                        config="--psm 6",
                        timeout=DEFAULT_OCR_TIMEOUT_SECONDS,
                    )
                except RuntimeError as exc:
                    last_error = str(exc)
                    continue
                except pytesseract.TesseractNotFoundError as exc:
                    return OcrResult(text="", status="unavailable", warning=str(exc) or "OCR 엔진을 사용할 수 없음")
                except pytesseract.TesseractError as exc:
                    last_error = str(exc)
                    continue
                normalized = normalize_space(text)
                if normalized:
                    return OcrResult(text=normalized, status="extracted", engine=f"tesseract:{lang}")
            return OcrResult(text="", status="empty", warning=last_error or "OCR 텍스트 없음")
    except Exception:
        return OcrResult(text="", status="error", warning=f"OCR 처리 실패 ({mime or 'image'})")
