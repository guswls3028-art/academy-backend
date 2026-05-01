"""LayoutStrategy 단위 테스트 — region_splitters.py.

각 strategy(Single/Dual/Quad)의 sort/x/y/clamp 동작을 독립적으로 락.
split_questions 통합은 test_question_splitter_t2_fixes.py + test_matchup_gold_regression.py
에서 검증.
"""
from __future__ import annotations

from academy.domain.tools.paper_type import PaperType, PaperTypeResult
from academy.domain.tools.question_splitter import TextBlock
from academy.domain.tools.region_splitters import (
    DUAL_STRATEGY,
    QUAD_STRATEGY,
    SINGLE_STRATEGY,
    DualColumnStrategy,
    QuadrantStrategy,
    SingleColumnStrategy,
    get_strategy_by_layout_flags,
    get_strategy_for_paper_type,
)


def _tb(text: str, x0: float, y0: float, x1: float = None, y1: float = None) -> TextBlock:
    if x1 is None:
        x1 = x0 + 100
    if y1 is None:
        y1 = y0 + 20
    return TextBlock(text=text, x0=x0, y0=y0, x1=x1, y1=y1)


# ── Strategy 매핑 ──

def test_get_strategy_by_layout_flags():
    assert isinstance(get_strategy_by_layout_flags(is_quad=True, is_dual=False), QuadrantStrategy)
    assert isinstance(get_strategy_by_layout_flags(is_quad=False, is_dual=True), DualColumnStrategy)
    assert isinstance(get_strategy_by_layout_flags(is_quad=False, is_dual=False), SingleColumnStrategy)
    # quad가 dual보다 우선
    assert isinstance(get_strategy_by_layout_flags(is_quad=True, is_dual=True), QuadrantStrategy)


def test_get_strategy_for_paper_type():
    quad_pt = PaperTypeResult(
        paper_type=PaperType.QUADRANT,
        confidence=1.0,
        is_dual_column=False,
        is_quadrant=True,
        is_handwriting_present=False,
        has_embedded_text=True,
    )
    dual_pt = PaperTypeResult(
        paper_type=PaperType.SCAN_DUAL,
        confidence=1.0,
        is_dual_column=True,
        is_quadrant=False,
        is_handwriting_present=False,
        has_embedded_text=False,
    )
    single_pt = PaperTypeResult(
        paper_type=PaperType.CLEAN_PDF_SINGLE,
        confidence=1.0,
        is_dual_column=False,
        is_quadrant=False,
        is_handwriting_present=False,
        has_embedded_text=True,
    )
    assert isinstance(get_strategy_for_paper_type(quad_pt), QuadrantStrategy)
    assert isinstance(get_strategy_for_paper_type(dual_pt), DualColumnStrategy)
    assert isinstance(get_strategy_for_paper_type(single_pt), SingleColumnStrategy)
    assert isinstance(get_strategy_for_paper_type(None), SingleColumnStrategy)


# ── SingleColumnStrategy ──

def test_single_strategy_sort_top_to_bottom():
    blocks = [_tb("c", 100, 300), _tb("a", 100, 100), _tb("b", 100, 200)]
    sorted_blocks = SINGLE_STRATEGY.sort_blocks(blocks, mid_x=500, mid_y=500)
    assert [b.text for b in sorted_blocks] == ["a", "b", "c"]


def test_single_strategy_x_range_full_width():
    block = _tb("1.", 100, 100)
    x0, x1 = SINGLE_STRATEGY.compute_x_range(block, page_width=1000, mid_x=500, margin=2)
    assert (x0, x1) == (0.0, 1000)


def test_single_strategy_y_end_normal():
    start = _tb("1.", 100, 100)
    next_b = _tb("2.", 100, 500)
    y_end = SINGLE_STRATEGY.compute_y_end(
        start, next_b, page_width=1000, page_height=1400, mid_x=500, mid_y=700, margin=2,
    )
    assert y_end == 498  # next.y0 - margin


def test_single_strategy_y_end_last_block():
    start = _tb("1.", 100, 100)
    y_end = SINGLE_STRATEGY.compute_y_end(
        start, None, page_width=1000, page_height=1400, mid_x=500, mid_y=700, margin=2,
    )
    assert y_end == 1400  # page_height


def test_single_strategy_y_end_strip_fallback():
    """next.y0 ≤ start.y0 (cross-column anchor) → page_height fallback."""
    start = _tb("1.", 100, 500)
    next_b = _tb("3.", 600, 100)  # 우측 column 위쪽
    y_end = SINGLE_STRATEGY.compute_y_end(
        start, next_b, page_width=1000, page_height=1400, mid_x=500, mid_y=700, margin=2,
    )
    assert y_end == 1400  # strip 방어


# ── DualColumnStrategy ──

def test_dual_strategy_sort_left_then_right():
    blocks = [
        _tb("right1", 600, 100),
        _tb("left1", 100, 100),
        _tb("left2", 100, 300),
        _tb("right2", 600, 300),
    ]
    sorted_blocks = DUAL_STRATEGY.sort_blocks(blocks, mid_x=500, mid_y=700)
    # 좌측 column 먼저 (top→bottom), 우측 column 다음 (top→bottom)
    assert [b.text for b in sorted_blocks] == ["left1", "left2", "right1", "right2"]


def test_dual_strategy_x_range_left_column():
    block = _tb("1.", 100, 100)
    x0, x1 = DUAL_STRATEGY.compute_x_range(block, page_width=1000, mid_x=500, margin=2)
    assert x0 == 0
    assert x1 == 502  # mid_x + margin


def test_dual_strategy_x_range_right_column():
    block = _tb("3.", 600, 100)
    x0, x1 = DUAL_STRATEGY.compute_x_range(block, page_width=1000, mid_x=500, margin=2)
    assert x0 == 498  # mid_x - margin
    assert x1 == 1000


def test_dual_strategy_y_end_same_column():
    start = _tb("1.", 100, 100)
    next_b = _tb("2.", 100, 500)
    y_end = DUAL_STRATEGY.compute_y_end(
        start, next_b, page_width=1000, page_height=1400, mid_x=500, mid_y=700, margin=2,
    )
    assert y_end == 498


def test_dual_strategy_y_end_column_change():
    """좌측 마지막 anchor → 우측 첫 anchor 전환 시 좌측은 page_height까지."""
    start = _tb("1.", 100, 500)
    next_b = _tb("3.", 600, 100)
    y_end = DUAL_STRATEGY.compute_y_end(
        start, next_b, page_width=1000, page_height=1400, mid_x=500, mid_y=700, margin=2,
    )
    assert y_end == 1400  # 컬럼 다르면 페이지 끝까지


def test_dual_strategy_post_clamp_left_column():
    block = _tb("1.", 100, 100)
    x0, x1 = DUAL_STRATEGY.post_clamp_x(block, x0=-10, x1=600, page_width=1000, mid_x=500, margin=2)
    assert x0 == 0  # 음수 클램프
    assert x1 == 502  # mid_x + margin 클램프


# ── QuadrantStrategy ──

def test_quad_strategy_sort_quadrant_order():
    """TL → TR → BL → BR."""
    blocks = [
        _tb("BR", 600, 800),
        _tb("TL", 100, 100),
        _tb("TR", 600, 100),
        _tb("BL", 100, 800),
    ]
    sorted_blocks = QUAD_STRATEGY.sort_blocks(blocks, mid_x=500, mid_y=700)
    assert [b.text for b in sorted_blocks] == ["TL", "TR", "BL", "BR"]


def test_quad_strategy_x_range_left():
    block = _tb("1.", 100, 100)
    x0, x1 = QUAD_STRATEGY.compute_x_range(block, page_width=1000, mid_x=500, margin=2)
    assert x0 == 0
    assert x1 == 502


def test_quad_strategy_y_end_same_quadrant():
    """같은 quadrant 안에서는 다음 anchor 직전까지."""
    start = _tb("1.", 100, 100)
    next_b = _tb("2.", 100, 300)  # 같은 TL quadrant
    y_end = QUAD_STRATEGY.compute_y_end(
        start, next_b, page_width=1000, page_height=1400, mid_x=500, mid_y=700, margin=2,
    )
    assert y_end == 298


def test_quad_strategy_y_end_top_to_bottom_quadrant():
    """top quadrant → bottom quadrant 전환 시 mid_y - margin까지."""
    start = _tb("TL", 100, 100)  # TL
    next_b = _tb("BL", 100, 800)  # BL (다른 quadrant)
    y_end = QUAD_STRATEGY.compute_y_end(
        start, next_b, page_width=1000, page_height=1400, mid_x=500, mid_y=700, margin=2,
    )
    assert y_end == 698  # mid_y - margin


def test_quad_strategy_y_end_bottom_quadrant_last():
    """bottom quadrant에서 다음 anchor가 다른 quadrant이면 page_height까지."""
    start = _tb("BL", 100, 800)
    next_b = _tb("BR", 600, 800)  # 같은 row but 다른 col
    y_end = QUAD_STRATEGY.compute_y_end(
        start, next_b, page_width=1000, page_height=1400, mid_x=500, mid_y=700, margin=2,
    )
    assert y_end == 1400  # bottom quadrant + 다른 quadrant → page_height


def test_quad_strategy_post_clamp_y_top_quadrant():
    """top quadrant에서 y1을 mid_y + margin으로 추가 클램프."""
    block = _tb("TL", 100, 100)
    y0, y1 = QUAD_STRATEGY.post_clamp_y(
        block, y0=98, y1=900,  # 이미 mid_y 넘어선 y1
        page_height=1400, mid_y=700, margin=2,
    )
    assert y1 == 702  # mid_y + margin
    assert y0 == 98


def test_quad_strategy_post_clamp_y_bottom_quadrant_unchanged():
    """bottom quadrant은 y1 클램프 안 함."""
    block = _tb("BL", 100, 800)
    y0, y1 = QUAD_STRATEGY.post_clamp_y(
        block, y0=798, y1=1400, page_height=1400, mid_y=700, margin=2,
    )
    assert (y0, y1) == (798, 1400)
