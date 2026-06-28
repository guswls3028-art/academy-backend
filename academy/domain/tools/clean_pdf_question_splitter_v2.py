"""Reading-flow based splitter for born-digital PDF question pages."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


_MAX_QUESTION_NUMBER = 500
_FOOTER_RE = re.compile(r"[-–—]?\d{1,3}(?:[/／]\d{1,3})?[-–—]?")
_MARGINAL_NUMBER_RE = re.compile(r"^\s*(\d{1,3})(?:\s+\1)?\s*\.?\s*$")
_QUESTION_START_RE = re.compile(
    r"^\s*(?:"
    r"(\d{1,3})\s+\1\s*[.)](?=\s|[가-힣A-Za-z(<【\[\"'“‘])"
    r"|"
    r"(\d{1,3})\s*[.)](?=\s|[가-힣A-Za-z(<【\[\"'“‘])"
    r"|"
    r"(\d{1,3})\s*/(?=\s|[가-힣A-Za-z(<【\[\"'“‘])"
    r"|"
    r"(?:문제|문)\s*(\d{1,3})\s*[.)]"
    r")"
)
_SOURCE_PREFIXED_RE = re.compile(
    r"^\s*[\[【]\s*[^\]】]{2,90}\s*[\]】]\s*(?:/|\s)*(.+)$",
    re.DOTALL,
)
_SOURCE_PREFIX_ONLY_RE = re.compile(r"^\s*[\[【]\s*[^\]】]{2,90}\s*[\]】]\s*$")
_SHARED_RANGE_RE = re.compile(
    r"^\s*[\[【(]\s*(\d{1,3})\s*(?:[,，~\-–]|및)\s*(\d{1,3})\s*[\]】)]?"
)
_SECTION_OFFSETS = {
    "서술": 100,
    "서답": 100,
    "논술": 200,
    "논답": 200,
    "단답": 300,
    "단술": 300,
    "약술": 400,
    "약답": 400,
}
_SECTION_RE = re.compile(
    r"^\s*\[?\s*"
    r"(서\s*술|서\s*답|논\s*술|논\s*답|단\s*답|단\s*술|약\s*술|약\s*답)"
    r"\s*형\s*[\]】)）]?\s*[\[【(（]?\s*(\d{1,3})"
)
_PREFIX_ONLY_RE = re.compile(
    r"^\s*(?:"
    r"[\[【(（]?\s*(?:객관식|선택형|서술형|논술형|단답형|약술형|주관식)\s*[\]】)）]?"
    r"|"
    r"[\[【]\s*[^\]】]{2,90}\s*[\]】]"
    r")\s*$"
)
_ANSWER_PAGE_RE = re.compile(r"(?:문제\s*해설|정답\s*(?:및\s*)?해설|정답|해설)")
_VISUAL_RE = re.compile(r"(?:그림|그래프|자료|모형|사진|도표)")
_SHORT_PROMPT_RE = re.compile(
    r"(?:빈\s*칸|써\s*넣으시오|써넣으시오|쓰시오|적으시오|구하시오|"
    r"서술하시오|설명하시오|답하시오|의미한다|나타낸다|\(\s*\))"
)
_WRITTEN_RE = re.compile(
    r"(?:써\s*넣으시오|써넣으시오|쓰시오|적으시오|구하시오|"
    r"서술하시오|설명하시오|답하시오)"
)
_REASONING_RE = re.compile(
    r"(?:(?:까닭|이유|원인).{0,40}(?:쓰시오|서술하시오|설명하시오|답하시오)|"
    r"(?:쓰시오|서술하시오|설명하시오|답하시오).{0,40}(?:까닭|이유|원인))"
)
_PRIOR_CONTEXT_RE = re.compile(
    r"(?:(?<![가-힣])위\s*(?:의\s*)?(?:실험|그림|자료|물체|문제|내용|보기|표)|"
    r"이를\s*토대로|해당\s*(?:자료|그림|실험))"
)


@dataclass(frozen=True)
class CleanPdfSplitRegion:
    number: int
    bbox: tuple[float, float, float, float]
    semantic_flags: tuple[str, ...]


@dataclass(frozen=True)
class CleanPdfSplitResult:
    handled: bool
    regions: tuple[CleanPdfSplitRegion, ...] = ()
    reason: str = ""


@dataclass(frozen=True)
class _Block:
    index: int
    text: str
    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def center_x(self) -> float:
        return (self.x0 + self.x1) / 2.0


@dataclass(frozen=True)
class _Anchor:
    number: int
    block: _Block
    start_y: float
    flow: str


def split_clean_pdf_questions_v2(
    text_blocks: list[Any],
    *,
    page_width: float,
    page_height: float,
    is_dual_hint: bool,
    is_quadrant_hint: bool,
    prefer_marginal: bool = False,
) -> CleanPdfSplitResult:
    """Return clean-PDF regions using explicit reading-flow boundaries.

    ``handled=False`` means the caller should keep the legacy splitter path.
    ``handled=True`` with no regions is an intentional non-question decision.
    """
    if is_quadrant_hint or not text_blocks or page_width <= 0 or page_height <= 0:
        return CleanPdfSplitResult(handled=False, reason="unsupported_layout")

    blocks = [_coerce_block(idx, block) for idx, block in enumerate(text_blocks)]
    blocks = [
        block for block in blocks
        if block.text and not _looks_like_footer(block, page_width, page_height)
    ]
    if not blocks:
        return CleanPdfSplitResult(handled=False, reason="empty_page")

    if prefer_marginal and _has_marginal_anchor_candidate(blocks, page_width, page_height):
        return CleanPdfSplitResult(handled=False, reason="prefer_marginal_legacy")

    body_anchor_candidates = [
        block for block in blocks
        if _extract_body_question_number(block.text) is not None
    ]
    if not body_anchor_candidates and _looks_like_answer_page(blocks, page_height):
        return CleanPdfSplitResult(handled=True, reason="answer_page")

    raw_anchors = _collect_anchors(blocks, page_width, page_height)
    if not raw_anchors:
        return CleanPdfSplitResult(handled=False, reason="no_anchors")

    anchors = _assign_flows(raw_anchors, blocks, page_width, is_dual_hint)
    regions = _build_regions(anchors, blocks, page_width, page_height)
    if not _passes_quality_gate(regions, page_width, page_height):
        return CleanPdfSplitResult(handled=False, reason="quality_gate")
    return CleanPdfSplitResult(handled=True, regions=tuple(regions), reason="ok")


def _coerce_block(index: int, raw: Any) -> _Block:
    return _Block(
        index=index,
        text=str(getattr(raw, "text", "") or "").strip(),
        x0=float(getattr(raw, "x0", 0.0) or 0.0),
        y0=float(getattr(raw, "y0", 0.0) or 0.0),
        x1=float(getattr(raw, "x1", 0.0) or 0.0),
        y1=float(getattr(raw, "y1", 0.0) or 0.0),
    )


def _looks_like_footer(block: _Block, page_width: float, page_height: float) -> bool:
    if block.y0 < page_height * 0.86:
        return False
    text = re.sub(r"\s+", "", block.text)
    return bool(text and len(text) <= 12 and _FOOTER_RE.fullmatch(text))


def _extract_body_question_number(text: str) -> int | None:
    text = (text or "").strip()
    if not text:
        return None

    shared = _SHARED_RANGE_RE.match(text)
    if shared:
        try:
            start = int(shared.group(1))
            end = int(shared.group(2))
        except ValueError:
            start = end = 0
        if 1 <= start < end <= _MAX_QUESTION_NUMBER and end - start <= 10:
            return start

    section = _SECTION_RE.match(text)
    if section:
        key = re.sub(r"\s+", "", section.group(1))[:2]
        try:
            number = int(section.group(2))
        except ValueError:
            return None
        if 1 <= number <= _MAX_QUESTION_NUMBER:
            return (_SECTION_OFFSETS.get(key) or 0) + number

    match = _QUESTION_START_RE.match(text)
    if not match:
        source_prefixed = _SOURCE_PREFIXED_RE.match(text)
        if source_prefixed:
            match = _QUESTION_START_RE.match(source_prefixed.group(1).strip())
    if not match:
        return None

    for group in match.groups():
        if group is None:
            continue
        try:
            number = int(group)
        except ValueError:
            continue
        if 1 <= number <= _MAX_QUESTION_NUMBER:
            return number
    return None


def _extract_marginal_number(text: str) -> int | None:
    first_line = (text or "").strip().split("\n", 1)[0].strip()
    if not first_line or len(first_line) > 5:
        return None
    match = _MARGINAL_NUMBER_RE.match(first_line)
    if not match:
        return None
    try:
        number = int(match.group(1))
    except ValueError:
        return None
    if 1 <= number <= _MAX_QUESTION_NUMBER:
        return number
    return None


def _looks_like_answer_page(blocks: list[_Block], page_height: float) -> bool:
    top_text = " ".join(
        block.text for block in blocks
        if block.y0 <= page_height * 0.24
    )
    if not _ANSWER_PAGE_RE.search(top_text):
        return False
    parenthesized_rows = sum(
        1 for block in blocks
        if re.match(r"^\s*\(\d{1,3}\)", block.text)
    )
    return parenthesized_rows >= 1 or len(top_text) >= 4


def _collect_anchors(
    blocks: list[_Block],
    page_width: float,
    page_height: float,
) -> list[tuple[int, _Block]]:
    anchors: list[tuple[int, _Block]] = []
    for block in sorted(blocks, key=lambda item: (item.y0, item.x0)):
        number = _extract_body_question_number(block.text)
        if number is not None:
            anchors.append((number, block))
            continue

        number = _extract_marginal_number(block.text)
        if number is None:
            continue
        if not _is_marginal_position(block, page_width):
            continue
        if not _standalone_has_question_body(block, blocks, page_width, page_height):
            continue
        anchors.append((number, block))

    seen: set[int] = set()
    deduped: list[tuple[int, _Block]] = []
    for number, block in anchors:
        if number in seen:
            continue
        seen.add(number)
        deduped.append((number, block))
    return deduped


def _has_marginal_anchor_candidate(
    blocks: list[_Block],
    page_width: float,
    page_height: float,
) -> bool:
    for block in blocks:
        number = _extract_marginal_number(block.text)
        if number is None:
            continue
        if _has_body_after_marginal_line(block.text):
            continue
        if not _is_marginal_position(block, page_width):
            continue
        if _standalone_has_question_body(block, blocks, page_width, page_height):
            return True
    return False


def _has_body_after_marginal_line(text: str) -> bool:
    lines = [line.strip() for line in (text or "").splitlines()]
    if len(lines) < 2:
        return False
    tail = " ".join(lines[1:]).strip()
    return bool(re.search(r"[가-힣A-Za-z]{3,}", tail))


def _is_marginal_position(block: _Block, page_width: float) -> bool:
    if page_width <= 0:
        return False
    mid_x = page_width * 0.5
    if block.x0 <= page_width * 0.15:
        return True
    return block.center_x >= mid_x and block.x0 <= mid_x + page_width * 0.15


def _standalone_has_question_body(
    anchor: _Block,
    blocks: list[_Block],
    page_width: float,
    page_height: float,
) -> bool:
    tail = "\n".join(anchor.text.splitlines()[1:]).strip()
    if re.search(r"[가-힣A-Za-z]{3,}", tail):
        return True

    line_tolerance = max(10.0, page_height * 0.045)
    for other in blocks:
        if other.index == anchor.index:
            continue
        if not _same_coarse_column(anchor, other, page_width):
            continue
        if other.y0 < anchor.y0 - 2.0 or other.y0 - anchor.y0 > line_tolerance:
            continue
        if other.x1 < anchor.x0 - page_width * 0.03:
            continue
        if re.search(r"[가-힣A-Za-z]{4,}", other.text):
            return True
    return False


def _same_coarse_column(left: _Block, right: _Block, page_width: float) -> bool:
    if page_width <= 0:
        return True
    mid_x = page_width * 0.5
    return (left.center_x < mid_x) == (right.center_x < mid_x)


def _assign_flows(
    raw_anchors: list[tuple[int, _Block]],
    blocks: list[_Block],
    page_width: float,
    is_dual_hint: bool,
) -> list[_Anchor]:
    mid_x = page_width * 0.5
    left_count = sum(1 for _, block in raw_anchors if block.center_x < mid_x)
    right_count = len(raw_anchors) - left_count
    effective_dual = is_dual_hint and left_count > 0 and right_count > 0

    anchors: list[_Anchor] = []
    for number, block in raw_anchors:
        start_y = _region_start_y(block, blocks, page_width)
        flow = "full"
        if effective_dual and not _anchor_has_full_width_context(block, blocks, page_width):
            flow = "left" if block.center_x < mid_x else "right"
        anchors.append(_Anchor(number=number, block=block, start_y=start_y, flow=flow))
    return anchors


def _region_start_y(anchor: _Block, blocks: list[_Block], page_width: float) -> float:
    candidates = [anchor.y0]
    max_gap = 28.0
    for block in blocks:
        if block.index == anchor.index:
            continue
        if block.y1 > anchor.y0 + 1.0:
            continue
        if anchor.y0 - block.y1 > max_gap:
            continue
        if not _same_coarse_column(anchor, block, page_width):
            continue
        if _PREFIX_ONLY_RE.match(block.text) or _SOURCE_PREFIX_ONLY_RE.match(block.text):
            candidates.append(block.y0)
    return min(candidates)


def _anchor_has_full_width_context(
    anchor: _Block,
    blocks: list[_Block],
    page_width: float,
) -> bool:
    if page_width <= 0:
        return False
    if anchor.x1 - anchor.x0 >= page_width * 0.62:
        return True
    band_top = anchor.y0 - 4.0
    band_bottom = anchor.y1 + 36.0
    band_blocks = [
        block for block in blocks
        if block.y0 <= band_bottom and block.y1 >= band_top
    ]
    for block in band_blocks:
        width = block.x1 - block.x0
        if width >= page_width * 0.62:
            return True
        if block.x0 <= page_width * 0.18 and block.x1 >= page_width * 0.82:
            return True
    return False


def _build_regions(
    anchors: list[_Anchor],
    blocks: list[_Block],
    page_width: float,
    page_height: float,
) -> list[CleanPdfSplitRegion]:
    regions: list[CleanPdfSplitRegion] = []
    by_flow: dict[str, list[_Anchor]] = {}
    for anchor in anchors:
        by_flow.setdefault(anchor.flow, []).append(anchor)
    for flow_anchors in by_flow.values():
        flow_anchors.sort(key=lambda item: (item.start_y, item.block.x0))

    margin_x = max(7.0, page_width * 0.012)
    margin_y = max(10.0, page_height * 0.015)
    footer_top = page_height * 0.92

    for anchor in anchors:
        next_anchor = _next_anchor_in_flow(anchor, by_flow[anchor.flow])
        y0 = max(0.0, anchor.start_y - margin_y)
        y1 = footer_top
        if next_anchor is not None:
            y1 = min(y1, max(y0 + page_height * 0.035, next_anchor.start_y - margin_y))

        x0, x1 = _flow_x_range(anchor, page_width, margin_x)
        flow_x0, flow_x1 = x0, x1
        content = _content_blocks(blocks, x0, y0, x1, y1)
        if content:
            text = " ".join(block.text for block in content)
            content_x0 = min(block.x0 for block in content)
            content_x1 = max(block.x1 for block in content)
            content_y1 = max(block.y1 for block in content)
            x0 = max(x0, content_x0 - margin_x)
            x1 = min(x1, content_x1 + margin_x)
            min_height = page_height * (0.055 if _SHORT_PROMPT_RE.search(text) else 0.08)
            if _VISUAL_RE.search(text):
                min_height = max(min_height, page_height * 0.18)
            y1 = min(y1, max(content_y1 + margin_y * 1.5, y0 + min_height))
        else:
            text = anchor.block.text

        flags = set(_semantic_flags(text))
        if anchor.flow == "right" and "visual_context" in flags:
            x0, x1 = flow_x0, flow_x1

        if anchor.flow == "full" and x1 - x0 < page_width * 0.62:
            center = (x0 + x1) / 2.0
            half_width = page_width * 0.31
            x0 = max(0.0, center - half_width)
            x1 = min(page_width, center + half_width)

        flags.add("clean_pdf_v2")
        flags.add(f"flow_{anchor.flow}")
        regions.append(
            CleanPdfSplitRegion(
                number=anchor.number,
                bbox=(x0, y0, x1, y1),
                semantic_flags=tuple(sorted(flags)),
            )
        )

    regions.sort(key=lambda item: item.number)
    return regions


def _next_anchor_in_flow(anchor: _Anchor, flow_anchors: list[_Anchor]) -> _Anchor | None:
    later = [
        other for other in flow_anchors
        if other is not anchor and other.start_y > anchor.start_y + 1.0
    ]
    if not later:
        return None
    return min(later, key=lambda item: item.start_y)


def _flow_x_range(anchor: _Anchor, page_width: float, margin: float) -> tuple[float, float]:
    if anchor.flow == "left":
        return 0.0, page_width * 0.5 + margin
    if anchor.flow == "right":
        return page_width * 0.5 - margin, page_width
    return 0.0, page_width


def _content_blocks(
    blocks: list[_Block],
    x0: float,
    y0: float,
    x1: float,
    y1: float,
) -> list[_Block]:
    content: list[_Block] = []
    for block in blocks:
        if block.y1 < y0 or block.y0 > y1:
            continue
        overlap_x = max(0.0, min(x1, block.x1) - max(x0, block.x0))
        block_width = max(1.0, block.x1 - block.x0)
        if overlap_x / block_width < 0.35:
            continue
        content.append(block)
    return content


def _semantic_flags(text: str) -> set[str]:
    flags: set[str] = set()
    if _VISUAL_RE.search(text):
        flags.add("visual_context")
    if _WRITTEN_RE.search(text):
        flags.add("written_response")
    if _REASONING_RE.search(text):
        flags.add("reasoning_response")
    if _SHORT_PROMPT_RE.search(text):
        flags.add("short_workbook_prompt")
    if _PRIOR_CONTEXT_RE.search(text):
        flags.add("references_prior_context")
    return flags


def _passes_quality_gate(
    regions: list[CleanPdfSplitRegion],
    page_width: float,
    page_height: float,
) -> bool:
    if not regions or len(regions) > 12:
        return False
    seen: set[int] = set()
    for region in regions:
        if region.number in seen:
            return False
        seen.add(region.number)
        x0, y0, x1, y1 = region.bbox
        if x0 < -1.0 or y0 < -1.0 or x1 > page_width + 1.0 or y1 > page_height + 1.0:
            return False
        if x1 - x0 < page_width * 0.12:
            return False
        min_height = page_height * 0.04
        if "short_workbook_prompt" in region.semantic_flags:
            min_height = page_height * 0.025
        if y1 - y0 < min_height:
            return False
    return True
