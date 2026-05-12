"""Stage 6.3F-2 — 운영 OCRTextBlock → mock unified schema normalizer.

Stage 6.3F-OCR 정적 schema diff 결과 (mock vs operating google_ocr_blocks):
- bbox 형식: mock (x, y, w, h normalized 0~1) vs 운영 (x0, y0, x1, y1 픽셀 corner) — high
- confidence: mock float / 운영 absent — medium
- page_metadata: mock OcrPageResult.page_index / 운영 미surface — medium

본 모듈은 위 3개 차이를 흡수하는 **변환 layer** — 실 OCR import 0회 (운영
OCRTextBlock 도 직접 import 안 함, duck-type 으로 처리).

원칙 (사용자 directive Stage 6.3F-2):
- 실 OCR / VLM 호출 0회
- DB write 0회 / Proposal INSERT 0회 / MatchupProblem 0회
- callback / R2 write / selected_problem_ids 변경 0회
- 운영 segment_dispatcher / proposal_helpers 미import
- credential / signed URL / raw image / full text 미저장 (운영 helper 의 sanitization 정책 준수)

duck-typed input — `real_block` 은 `text`, `x0`, `y0`, `x1`, `y1` 속성만 가지면 됨.
실제 운영 dataclass (`academy.adapters.ai.ocr.google.OCRTextBlock`) 또는 호환 형식 모두 OK.
"""
from __future__ import annotations

from typing import Any, Iterable, Optional

from .mock_response_integrator import (
    MockOcrResponse, OcrPageResult, OcrTextBlock, UnifiedCandidate,
    _ocr_response_to_unified,
)


SCHEMA_VERSION = "6.3F-2-ocr-normalizer-1"


# ── 핵심 변환 함수 ────────────────────────────────────────────


def normalize_pixel_corner_to_norm_xywh(
    x0: float, y0: float, x1: float, y1: float,
    *,
    page_width: float,
    page_height: float,
) -> tuple[float, float, float, float]:
    """pixel corner (x0, y0, x1, y1) → normalized (x, y, w, h) 0~1.

    page_width / page_height 는 호출자가 page render 시점에 알고 있어야 함
    (운영 helper 미surface — Stage 6.3F-OCR finding).

    Raises:
        ValueError: page_width 또는 page_height 가 0 이하인 경우.
        ValueError: x1 < x0 또는 y1 < y0 (corner 순서 위반) 인 경우.
    """
    if page_width <= 0 or page_height <= 0:
        raise ValueError(
            f"page_width and page_height must be > 0; "
            f"got page_width={page_width}, page_height={page_height}"
        )
    if x1 < x0 or y1 < y0:
        raise ValueError(
            f"corner order violation: x1={x1} < x0={x0} or y1={y1} < y0={y0}"
        )
    try:
        x = float(x0) / float(page_width)
        y = float(y0) / float(page_height)
        w = (float(x1) - float(x0)) / float(page_width)
        h = (float(y1) - float(y0)) / float(page_height)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"non-numeric bbox values: {exc}") from exc
    return (x, y, w, h)


def real_ocr_block_to_mock_block(
    real_block: Any,
    *,
    page_width: float,
    page_height: float,
    confidence: Optional[float] = None,
) -> OcrTextBlock:
    """운영 OCRTextBlock duck-typed input → mock_response_integrator.OcrTextBlock.

    Args:
        real_block: text / x0 / y0 / x1 / y1 속성 가진 객체.
        page_width / page_height: 호출자 책임 (운영 helper 미surface).
        confidence: 운영 helper 가 surface 안 하므로 default None. 호출자가
            raw protobuf 등 외부에서 추출 가능 시 명시 주입.

    Returns:
        OcrTextBlock — bbox_norm (0~1 xywh), text, confidence (Optional)

    Raises:
        AttributeError: real_block 이 필요 속성 없을 경우.
        ValueError: bbox 변환 실패 (page_width/height 0 등).
    """
    text = getattr(real_block, "text", None) or ""
    x0 = getattr(real_block, "x0")
    y0 = getattr(real_block, "y0")
    x1 = getattr(real_block, "x1")
    y1 = getattr(real_block, "y1")
    bbox_norm = normalize_pixel_corner_to_norm_xywh(
        x0, y0, x1, y1,
        page_width=page_width, page_height=page_height,
    )
    return OcrTextBlock(
        bbox_norm=bbox_norm,
        text=text,
        confidence=confidence,
    )


def real_ocr_blocks_to_mock_response(
    real_blocks: Iterable[Any],
    *,
    page_index: int,
    page_width: float,
    page_height: float,
    pdf_path: str = "",
    engine: str = "google_cloud_vision",
    confidences: Optional[list[Optional[float]]] = None,
) -> MockOcrResponse:
    """운영 google_ocr_blocks 결과 list → MockOcrResponse (page_index 외부 주입).

    Args:
        real_blocks: 운영 OCR adapter 가 반환한 list (duck-typed).
        page_index: caller 가 책임지고 부여 (운영 helper 미surface).
        page_width / page_height: 동일 caller 책임.
        confidences: 운영 raw protobuf 에서 추출한 per-block confidence (선택).
            None 이면 모두 None 으로 처리.
        pdf_path / engine: metadata 만 (실 호출 X — 변환 결과는 is_mock=False 마킹).

    Returns:
        MockOcrResponse — synthetic mock 과 동일 schema 지만 변환 결과임을
        `is_mock=False` 로 표시 (downstream 이 source 구분 가능).

    Raises:
        ValueError: confidences 길이가 real_blocks 길이와 다를 때.
    """
    blocks_list = list(real_blocks)
    if confidences is not None and len(confidences) != len(blocks_list):
        raise ValueError(
            f"confidences length {len(confidences)} != real_blocks length "
            f"{len(blocks_list)}"
        )
    text_blocks: list[OcrTextBlock] = []
    for i, b in enumerate(blocks_list):
        conf = confidences[i] if confidences is not None else None
        text_blocks.append(real_ocr_block_to_mock_block(
            b, page_width=page_width, page_height=page_height, confidence=conf,
        ))
    return MockOcrResponse(
        engine=engine,
        pdf_path=pdf_path,
        page_count=1,
        pages=[OcrPageResult(page_index=int(page_index), text_blocks=text_blocks)],
        cost_actual_usd=0.0,
        is_mock=False,    # 운영 변환 결과 — synthetic mock 아님
    )


def real_ocr_blocks_to_unified_candidates(
    real_blocks: Iterable[Any],
    *,
    page_index: int,
    page_width: float,
    page_height: float,
    confidences: Optional[list[Optional[float]]] = None,
) -> list[UnifiedCandidate]:
    """운영 OCR blocks → UnifiedCandidate list (mock generator path 와 동등 출력).

    내부 흐름:
        real blocks → real_ocr_blocks_to_mock_response()
        → _ocr_response_to_unified()
        → list[UnifiedCandidate]

    UnifiedCandidate.source = "ocr" (mock_response_integrator 와 동등).
    confidence None → 0.0 fallback (mock_response_integrator._ocr_response_to_unified
    가 처리 — semantic: 미상 → 최저 신뢰도 안전 처리).
    """
    response = real_ocr_blocks_to_mock_response(
        real_blocks,
        page_index=page_index,
        page_width=page_width,
        page_height=page_height,
        confidences=confidences,
    )
    return _ocr_response_to_unified(response)
