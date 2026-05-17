"""Layout별 region bbox 계산 strategy — split_questions 인라인 분기 분리.

이전 상태: question_splitter.split_questions 함수(200줄+)에 quad/dual/single
분기가 인라인되어 단위 테스트 어려움. 새 layout 추가 시 if-elif가 비대해짐.

현재: paper_type → LayoutStrategy → bbox 계산. 각 strategy가 독립적으로
정렬·x range·y end·후처리 클램프를 책임. split_questions는 strategy를 호출만.

확장 포인트:
- 새 layout 추가 시 LayoutStrategy 서브클래스 추가
- paper_type 매핑은 get_strategy_for_paper_type 한 군데
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional, Tuple

from academy.domain.tools.paper_type import PaperTypeResult


class LayoutStrategy(ABC):
    """layout별 region bbox 계산 인터페이스.

    각 strategy는 반드시:
      1) sort_blocks    — 페이지 안 블록 정렬 순서 결정 (anchor 검색 순서)
      2) compute_x_range — region의 좌/우 경계 결정
      3) compute_y_end   — region의 하단 경계 결정 (다음 anchor 또는 페이지 끝)
    선택적으로:
      4) post_clamp_x   — x 경계 추가 클램프 (dual column 안에 강제)
      5) post_clamp_y   — y 경계 추가 클램프 (quadrant 안에 강제)
    """

    name: str = "abstract"

    @abstractmethod
    def sort_blocks(self, blocks, mid_x: float, mid_y: float) -> list:
        """블록 정렬 순서 — anchor 인식 순서가 layout 따라 다름."""

    @abstractmethod
    def compute_x_range(
        self,
        start_block,
        page_width: float,
        mid_x: float,
        margin: float,
    ) -> Tuple[float, float]:
        """region의 좌/우 경계 계산."""

    @abstractmethod
    def compute_y_end(
        self,
        start_block,
        next_block,
        page_width: float,
        page_height: float,
        mid_x: float,
        mid_y: float,
        margin: float,
    ) -> float:
        """region의 하단 경계 계산 (다음 anchor 또는 페이지 끝)."""

    def post_clamp_x(
        self,
        start_block,
        x0: float,
        x1: float,
        page_width: float,
        mid_x: float,
        margin: float,
    ) -> Tuple[float, float]:
        """기본: 추가 클램프 없음."""
        return (x0, x1)

    def post_clamp_y(
        self,
        start_block,
        y0: float,
        y1: float,
        page_height: float,
        mid_y: float,
        margin: float,
    ) -> Tuple[float, float]:
        """기본: 추가 클램프 없음."""
        return (y0, y1)


class SingleColumnStrategy(LayoutStrategy):
    """단일 컬럼 — 페이지 폭 전체 사용. 그림/표가 텍스트 폭 벗어나도 OK."""

    name = "single"

    def sort_blocks(self, blocks, mid_x: float, mid_y: float) -> list:
        return sorted(blocks, key=lambda b: (b.y0, b.x0))

    def compute_x_range(self, start_block, page_width, mid_x, margin):
        return (0.0, page_width)

    def compute_y_end(
        self, start_block, next_block, page_width, page_height, mid_x, mid_y, margin,
    ) -> float:
        if next_block is None:
            return page_height
        y_end = next_block.y0 - margin
        # Defensive: cross-column anchor (dual-col 미인식)에서 next.y0 < start.y0 →
        # strip 결함 차단. 운영 doc#177 q1 (h=63) 결함의 본질 fix.
        if y_end <= start_block.y0:
            return page_height
        return y_end


class DualColumnStrategy(LayoutStrategy):
    """2단 컬럼 — column 전체 width 사용. region_blocks가 anchor 1개만 포함해도
    strip(width<10%) 결함 안 됨.
    """

    name = "dual"

    @staticmethod
    def _is_left(block, mid_x: float) -> bool:
        # OCR may return a right-column sub-block whose x0 slightly crosses the
        # gutter after inline split. Use center for column ownership.
        return ((block.x0 + block.x1) / 2) < mid_x

    def sort_blocks(self, blocks, mid_x: float, mid_y: float) -> list:
        return sorted(
            blocks,
            key=lambda b: (0 if self._is_left(b, mid_x) else 1, b.y0, b.x0),
        )

    def compute_x_range(self, start_block, page_width, mid_x, margin):
        if self._is_left(start_block, mid_x):
            return (0.0, mid_x + margin)
        return (mid_x - margin, page_width)

    def compute_y_end(
        self, start_block, next_block, page_width, page_height, mid_x, mid_y, margin,
    ) -> float:
        if next_block is None:
            return page_height
        curr_in_left = self._is_left(start_block, mid_x)
        next_in_left = self._is_left(next_block, mid_x)
        if curr_in_left != next_in_left:
            # 컬럼이 다르면 현재 컬럼 끝까지 (그림/표 포함)
            return page_height
        # 같은 컬럼: 다음 anchor 직전까지
        y_end = next_block.y0 - margin
        # Defensive: sort 오류로 next.y0 ≤ start.y0 인 경우 (mid_x 경계에 걸쳐있는 anchor)
        if y_end <= start_block.y0:
            return page_height
        return y_end

    def post_clamp_x(self, start_block, x0, x1, page_width, mid_x, margin):
        if self._is_left(start_block, mid_x):
            return (max(0.0, x0), min(mid_x + margin, x1))
        return (max(mid_x - margin, x0), min(page_width, x1))


class QuadrantStrategy(LayoutStrategy):
    """4분할 (2x2 grid) — 각 region을 자기 quadrant 안에 강제 구속."""

    name = "quad"

    def sort_blocks(self, blocks, mid_x: float, mid_y: float) -> list:
        def _quad_order(b):
            row = 0 if b.y0 < mid_y else 1
            col = 0 if b.x0 < mid_x else 1
            return (row * 2 + col, b.y0, b.x0)

        return sorted(blocks, key=_quad_order)

    def compute_x_range(self, start_block, page_width, mid_x, margin):
        curr_left = start_block.x0 < mid_x
        if curr_left:
            return (0.0, mid_x + margin)
        return (mid_x - margin, page_width)

    def compute_y_end(
        self, start_block, next_block, page_width, page_height, mid_x, mid_y, margin,
    ) -> float:
        if next_block is None:
            return page_height
        curr_left = start_block.x0 < mid_x
        curr_top = start_block.y0 < mid_y
        next_left = next_block.x0 < mid_x
        next_top = next_block.y0 < mid_y
        same_quadrant = (curr_left == next_left) and (curr_top == next_top)
        if same_quadrant:
            return next_block.y0 - margin
        return mid_y - margin if curr_top else page_height

    def post_clamp_y(self, start_block, y0, y1, page_height, mid_y, margin):
        # quadrant 경계로 y 추가 클램프 — 상단 quadrant이면 mid_y 아래로 못 내려감
        curr_top = start_block.y0 < mid_y
        if curr_top:
            return (y0, min(y1, mid_y + margin))
        return (y0, y1)


SINGLE_STRATEGY = SingleColumnStrategy()
DUAL_STRATEGY = DualColumnStrategy()
QUAD_STRATEGY = QuadrantStrategy()


def get_strategy_for_paper_type(
    paper_type: Optional[PaperTypeResult],
) -> LayoutStrategy:
    """paper_type → LayoutStrategy 매핑.

    paper_type=None이면 SingleColumnStrategy (호출자가 휴리스틱 미적용 시 default).
    """
    if paper_type is None:
        return SINGLE_STRATEGY
    if paper_type.is_quadrant:
        return QUAD_STRATEGY
    if paper_type.is_dual_column:
        return DUAL_STRATEGY
    return SINGLE_STRATEGY


def get_strategy_by_layout_flags(
    *, is_quad: bool, is_dual: bool,
) -> LayoutStrategy:
    """layout boolean flags → LayoutStrategy 매핑 (split_questions 휴리스틱 fallback용)."""
    if is_quad:
        return QUAD_STRATEGY
    if is_dual:
        return DUAL_STRATEGY
    return SINGLE_STRATEGY
