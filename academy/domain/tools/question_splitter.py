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
        # No questions detected — return entire page as single region
        return [
            QuestionRegion(
                number=1,
                bbox=(0, 0, page_width, page_height),
                page_index=page_index,
            )
        ]

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
        x0 = max(0, min(b.x0 for b in region_blocks) - margin)
        y0 = max(0, start_block.y0 - margin)
        x1 = min(page_width, max(b.x1 for b in region_blocks) + margin)

        if i + 1 < len(question_starts):
            # End just before next question starts
            next_block = sorted_blocks[question_starts[i + 1][1]]

            if is_dual_column:
                # In dual column: if next question is in different column,
                # extend current to bottom of its column
                curr_in_left = start_block.x0 < mid_x
                next_in_left = next_block.x0 < mid_x
                if curr_in_left != next_in_left:
                    y1 = page_height
                else:
                    y1 = next_block.y0 - margin
            else:
                y1 = next_block.y0 - margin
        else:
            y1 = page_height

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
