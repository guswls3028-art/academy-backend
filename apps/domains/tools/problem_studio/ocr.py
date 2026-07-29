from __future__ import annotations

import io
import os
from dataclasses import dataclass
from typing import Any

from apps.domains.tools.problem_studio.structure import normalize_space


DEFAULT_OCR_TIMEOUT_SECONDS = 12
DEFAULT_OCR_MAX_UNITS = 8


@dataclass(frozen=True)
class OcrTextBlock:
    text: str
    x0: float
    y0: float
    x1: float
    y1: float


@dataclass(frozen=True)
class OcrResult:
    text: str
    status: str
    engine: str = "tesseract"
    warning: str = ""
    blocks: tuple[OcrTextBlock, ...] = ()


def _tesseract_line_blocks(data: dict[str, Any]) -> tuple[OcrTextBlock, ...]:
    grouped: dict[tuple[int, int, int], list[tuple[str, int, int, int, int]]] = {}
    text_values = data.get("text") or []
    for index, raw_text in enumerate(text_values):
        text = str(raw_text or "").strip()
        if not text:
            continue
        try:
            confidence = float((data.get("conf") or [])[index])
        except (IndexError, TypeError, ValueError):
            confidence = -1
        if confidence < 0:
            continue
        try:
            left = int((data.get("left") or [])[index])
            top = int((data.get("top") or [])[index])
            width = int((data.get("width") or [])[index])
            height = int((data.get("height") or [])[index])
            key = (
                int((data.get("block_num") or [])[index]),
                int((data.get("par_num") or [])[index]),
                int((data.get("line_num") or [])[index]),
            )
        except (IndexError, TypeError, ValueError):
            continue
        grouped.setdefault(key, []).append((text, left, top, width, height))

    blocks: list[OcrTextBlock] = []
    for words in grouped.values():
        x0 = min(word[1] for word in words)
        y0 = min(word[2] for word in words)
        x1 = max(word[1] + word[3] for word in words)
        y1 = max(word[2] + word[4] for word in words)
        blocks.append(OcrTextBlock(
            text=" ".join(word[0] for word in words),
            x0=float(x0),
            y0=float(y0),
            x1=float(x1),
            y1=float(y1),
        ))
    return tuple(sorted(blocks, key=lambda block: (block.y0, block.x0)))


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
                    data = pytesseract.image_to_data(
                        image,
                        lang=lang,
                        config="--psm 6",
                        output_type=pytesseract.Output.DICT,
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
                blocks = _tesseract_line_blocks(data)
                normalized = normalize_space("\n".join(block.text for block in blocks))
                if normalized:
                    return OcrResult(
                        text=normalized,
                        status="extracted",
                        engine=f"tesseract:{lang}",
                        blocks=blocks,
                    )
            return OcrResult(text="", status="empty", warning=last_error or "OCR 텍스트 없음")
    except Exception:
        return OcrResult(text="", status="error", warning=f"OCR 처리 실패 ({mime or 'image'})")
