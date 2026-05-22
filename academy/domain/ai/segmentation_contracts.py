"""Pure AI segmentation DTOs shared by application services and adapters.

This module intentionally has no Django, SDK, repository, or use-case imports.
Adapters may depend on these contracts without depending on application use cases.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class OcrTextBlock:
    """OCR text block in normalized page coordinates."""

    bbox_norm: tuple[float, float, float, float]
    text: str
    confidence: Optional[float] = None


@dataclass
class OcrPageResult:
    page_index: int
    text_blocks: list[OcrTextBlock] = field(default_factory=list)


@dataclass
class MockOcrResponse:
    engine: str
    pdf_path: str
    page_count: int
    pages: list[OcrPageResult] = field(default_factory=list)
    cost_actual_usd: float = 0.0
    is_mock: bool = True


@dataclass
class VlmDetectedProblem:
    number: Optional[int]
    bbox_norm: tuple[float, float, float, float]
    confidence: float = 0.80


@dataclass
class VlmPageResult:
    page_index: int
    detected_problems: list[VlmDetectedProblem] = field(default_factory=list)


@dataclass
class MockVlmResponse:
    engine: str
    pdf_path: str
    pages: list[VlmPageResult] = field(default_factory=list)
    cost_actual_usd: float = 0.0
    is_mock: bool = True


@dataclass
class UnifiedCandidate:
    page_index: int
    bbox_norm: tuple[float, float, float, float]
    number: Optional[int]
    source: str
    confidence: Optional[float]
    debug: dict = field(default_factory=dict)


@dataclass
class ValidationError:
    code: str
    detail: str
    bbox_iou: Optional[float] = None


@dataclass
class ProposalPayloadCandidate:
    tenant_id: int
    document_id: int
    page_number: int
    detected_problem_number: int
    bbox: dict
    engine: str
    model_version: str = ""
    confidence: float = 0.0
    status: str = "pending"
    analysis_version_key: str = ""
    image_key: str = ""
    raw_response: dict = field(default_factory=dict)
    validation_errors: list[ValidationError] = field(default_factory=list)


def _bbox_iou_norm(
    a: tuple[float, float, float, float],
    b: tuple[float, float, float, float],
) -> float:
    """Return IoU for normalized (x, y, w, h) boxes."""
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    if aw <= 0 or ah <= 0 or bw <= 0 or bh <= 0:
        return 0.0
    ix1, iy1 = max(ax, bx), max(ay, by)
    ix2, iy2 = min(ax + aw, bx + bw), min(ay + ah, by + bh)
    iw, ih = max(0, ix2 - ix1), max(0, iy2 - iy1)
    inter = iw * ih
    union = aw * ah + bw * bh - inter
    return inter / union if union > 0 else 0.0


def _ocr_response_to_unified(resp: MockOcrResponse) -> list[UnifiedCandidate]:
    """Convert OCR response blocks into unified candidates."""
    import re

    out: list[UnifiedCandidate] = []
    num_re = re.compile(r"^(\d+)\.\s")
    for page in resp.pages:
        for blk in page.text_blocks:
            m = num_re.match(blk.text or "")
            number = int(m.group(1)) if m else None
            out.append(
                UnifiedCandidate(
                    page_index=page.page_index,
                    bbox_norm=tuple(blk.bbox_norm),
                    number=number,
                    source="ocr",
                    confidence=blk.confidence,
                    debug={
                        "text_preview": (blk.text or "")[:40],
                        "confidence_raw_present": blk.confidence is not None,
                    },
                )
            )
    return out


def _vlm_response_to_unified(resp: MockVlmResponse) -> list[UnifiedCandidate]:
    """Convert VLM response problems into unified candidates."""
    out: list[UnifiedCandidate] = []
    for page in resp.pages:
        for prob in page.detected_problems:
            out.append(
                UnifiedCandidate(
                    page_index=page.page_index,
                    bbox_norm=tuple(prob.bbox_norm),
                    number=prob.number,
                    source="vlm",
                    confidence=prob.confidence,
                )
            )
    return out
