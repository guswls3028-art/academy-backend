# PATH: academy/domain/tools/question_splitter.py
# Rule-based question splitting from PDF pages.
#
# Detects question number patterns in extracted text blocks and
# determines bounding regions for each question.

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional, Tuple


@dataclass
class TextBlock:
    """A block of text with its bounding box on the page."""
    text: str
    x0: float
    y0: float
    x1: float
    y1: float


def is_non_question_page(blocks: List[TextBlock]) -> bool:
    """
    비문항 페이지 감지 — 표지, 진도표, 안내문, 정답지, 해설지 등.

    휴리스틱:
    - 정답지/해설지 패턴 감지 → True
    - 문항 지시문이나 보기 번호가 있으면 → False (문항 페이지)
    - 비문항 키워드가 여러 개 있으면 → True
    """
    full_text = " ".join(b.text for b in blocks).strip()
    if not full_text:
        return True

    # 정답지 감지 (최우선): "⑴ × ⑵ O" "⑴ ② ⑵ ④" 같은 패턴 반복
    answer_pattern = re.findall(r"[⑴⑵⑶⑷⑸⑹⑺⑻⑼]\s*[×OX①②③④⑤]", full_text)
    if len(answer_pattern) >= 5:
        return True

    # 해설지 감지: "번호. ⑴ ...이다." 소문항 패턴
    sub_q_pattern = re.findall(r"\d+\.\s*[⑴⑵⑶⑷⑸⑹⑺⑻⑼]", full_text)
    if len(sub_q_pattern) >= 2:
        question_indicators_early = [
            "옳은 것", "구하시오", "표시하시오", "고르시오", "서술하시오",
            "풀이 과정", "이에 대한 설명", "다음 중", "보기에서",
        ]
        if not any(kw in full_text for kw in question_indicators_early):
            return True

    # 문항 페이지 강력 지표: 보기 번호 패턴
    choice_patterns = ["①", "②", "③", "④", "⑤", "ㄱ.", "ㄴ.", "ㄷ."]
    has_choices = any(p in full_text for p in choice_patterns)

    question_indicators = [
        "옳은 것", "구하시오", "표시하시오", "고르시오", "서술하시오",
        "풀이 과정", "이에 대한 설명", "다음 중", "보기에서",
    ]
    has_question_indicator = any(kw in full_text for kw in question_indicators)

    if has_choices or has_question_indicator:
        return False

    # 설명조 종결어미 빈도 기반 해설지 감지
    explanation_markers = re.findall(
        r"(?:이므로|때문이다|따라서|그러므로|해설|나타난다|관측된다|생성된다)",
        full_text,
    )
    if len(explanation_markers) >= 3 and not has_question_indicator:
        return True

    # 비문항 지표: 진도표, 강의방침, 안내 등
    non_question_indicators = [
        "진도", "운영 방침", "재시험", "클리닉", "홈페이지",
        "대단원", "중단원", "세부 내용", "난이도",
        "주차", "복습과제", "워크북",
    ]
    non_q_count = sum(1 for kw in non_question_indicators if kw in full_text)
    if non_q_count >= 3:
        return True

    return False


@dataclass
class QuestionRegion:
    """A detected question region on a page."""
    number: int
    bbox: Tuple[float, float, float, float]  # (x0, y0, x1, y1)
    page_index: int


# Question number patterns (Korean exam style)
# Matches: "1.", "2.", "1)", "2)", "(1)", "(2)", "[1]", "[2]"
_QUESTION_PATTERN = re.compile(
    r"^\s*(?:"
    r"(\d{1,3})\s*[.)]\s"           # "1." or "1) "
    r"|"
    r"\((\d{1,3})\)\s"              # "(1) "
    r"|"
    r"\[(\d{1,3})\]\s"              # "[1] "
    r"|"
    r"(?:문제|문)\s*(\d{1,3})\s*[.)]"  # "문제1." or "문 1)"
    r")"
)


def _extract_question_number(text: str) -> Optional[int]:
    """Extract question number from text block content.

    Returns:
        Question number if found, None otherwise.
    """
    text = text.strip()
    if not text:
        return None

    m = _QUESTION_PATTERN.match(text)
    if not m:
        return None

    # One of the groups will have the number
    for g in m.groups():
        if g is not None:
            try:
                num = int(g)
                if 1 <= num <= 200:  # reasonable range
                    return num
            except ValueError:
                continue
    return None


def _detect_column_layout(
    blocks: List[TextBlock],
    page_width: float,
) -> bool:
    """Detect if the page has a dual-column layout.

    Heuristic: if >30% of text blocks have x0 > page_width * 0.45,
    the page likely has two columns.
    """
    if not blocks or page_width <= 0:
        return False

    mid_x = page_width * 0.45
    right_count = sum(1 for b in blocks if b.x0 > mid_x)
    return right_count > len(blocks) * 0.3


def split_questions(
    text_blocks: List[TextBlock],
    page_width: float,
    page_height: float,
    page_index: int = 0,
) -> List[QuestionRegion]:
    """Split a page into question regions based on detected question numbers.

    Args:
        text_blocks: Text blocks extracted from the page with positions.
        page_width: Page width in points.
        page_height: Page height in points.
        page_index: Index of the page in the PDF.

    Returns:
        List of QuestionRegion sorted by question number.
    """
    if not text_blocks:
        return []

    is_dual_column = _detect_column_layout(text_blocks, page_width)
    mid_x = page_width * 0.5

    # Sort blocks by layout order:
    # - Single column: top to bottom
    # - Dual column: left column top-to-bottom, then right column top-to-bottom
    if is_dual_column:
        sorted_blocks = sorted(
            text_blocks,
            key=lambda b: (0 if b.x0 < mid_x else 1, b.y0, b.x0),
        )
    else:
        sorted_blocks = sorted(text_blocks, key=lambda b: (b.y0, b.x0))

    # Find question start positions
    question_starts: List[Tuple[int, int]] = []  # (question_number, block_index)
    for idx, block in enumerate(sorted_blocks):
        qnum = _extract_question_number(block.text)
        if qnum is not None:
            question_starts.append((qnum, idx))

    if not question_starts:
        # No questions detected — skip this page (table of contents, cover, etc.)
        return []

    # Build regions: each question spans from its start to the next question start
    regions: List[QuestionRegion] = []
    margin = 2.0  # small margin in points

    for i, (qnum, start_idx) in enumerate(question_starts):
        # Region starts at current question block
        start_block = sorted_blocks[start_idx]

        # Determine end boundary
        if i + 1 < len(question_starts):
            next_start_idx = question_starts[i + 1][1]
            # Collect all blocks from start to just before next question
            region_blocks = sorted_blocks[start_idx:next_start_idx]
        else:
            # Last question: extends to end of page
            region_blocks = sorted_blocks[start_idx:]

        if not region_blocks:
            continue

        # Calculate bounding box from all blocks in this region
        y0 = max(0, start_block.y0 - margin)

        if is_dual_column:
            # Dual column: use actual text block bounds
            x0 = max(0, min(b.x0 for b in region_blocks) - margin)
            x1 = min(page_width, max(b.x1 for b in region_blocks) + margin)
        else:
            # Single column: use full page width (images/diagrams may extend beyond text)
            x0 = 0
            x1 = page_width

        # ── 하단 종료점 결정 ──
        # 텍스트 블록 기반 bottom (비텍스트 요소는 텍스트 블록에 안 잡힘)
        text_bottom = max(b.y1 for b in region_blocks)
        # 비텍스트 콘텐츠(그림/표/수식)를 위한 확장 여유 (텍스트 높이의 50%)
        avg_block_height = sum(b.y1 - b.y0 for b in region_blocks) / len(region_blocks)
        content_padding = max(margin * 4, avg_block_height * 0.5)

        if i + 1 < len(question_starts):
            # End just before next question starts
            next_block = sorted_blocks[question_starts[i + 1][1]]

            if is_dual_column:
                curr_in_left = start_block.x0 < mid_x
                next_in_left = next_block.x0 < mid_x
                if curr_in_left != next_in_left:
                    y1 = text_bottom + content_padding
                else:
                    # 다음 문항 시작까지 — 텍스트 bottom + padding이 더 클 수 있음
                    y1 = min(next_block.y0 - margin, text_bottom + content_padding)
                    # 최소한 텍스트 bottom은 포함
                    y1 = max(y1, text_bottom + margin)
            else:
                # 다음 문항 직전까지가 상한, 텍스트 bottom + padding이 하한
                y1_next = next_block.y0 - margin
                y1_content = text_bottom + content_padding
                y1 = min(y1_next, y1_content)
                y1 = max(y1, text_bottom + margin)
        else:
            # 마지막 문항: 텍스트 bottom + 넉넉한 패딩 (비텍스트 요소 포함)
            y1 = text_bottom + content_padding * 1.5

        y1 = min(page_height, max(y1, y0 + 10))

        # For dual column, constrain x to the appropriate column
        if is_dual_column:
            if start_block.x0 < mid_x:
                x0 = max(0, x0)
                x1 = min(mid_x + margin, x1)
            else:
                x0 = max(mid_x - margin, x0)
                x1 = min(page_width, x1)

        regions.append(
            QuestionRegion(
                number=qnum,
                bbox=(x0, y0, x1, y1),
                page_index=page_index,
            )
        )

    # Sort by question number, fallback to layout order
    regions.sort(key=lambda r: r.number)

    return regions
