# PATH: academy/domain/tools/question_splitter.py
# Rule-based question splitting from PDF pages.
#
# Detects question number patterns in extracted text blocks and
# determines bounding regions for each question.

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional, Tuple


# 시험지 문항 번호 현실 상한.
# 통합과학/화학/물리 등 정기고사 최대 관측: ~32.
# 여유를 두고 60으로 상한 (수능형/심화 포함).
# 이 상한을 넘는 OCR 결과는 오인식(예: "ㄷ8"→128)으로 간주해 거부.
_MAX_LEGIT_QUESTION_NUMBER = 60


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
    - 디자인 표지(WORKBOOK/PROJECT 류) → True
    - 시험지 헤더(제 N 교시 / 탐구 영역 / 홀수형) → True
    - 학습자료 챕터 표지(객서심화 / 메인자료 / 복습과제 등) → True
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

    # ── 디자인 표지 페이지 감지 (학습자료/문제집 표지) ──
    # 운영 케이스 (Tenant 2): "Runner's High with God Min", "신과 함께 PROJECT WORKBOOK",
    # "1학기 중간고사 대비 문항편", "객·서 최종대비", "복습과제", "메인자료" 등.
    # 텍스트 양은 적고(< 300자), 디자인 키워드가 있으면 표지로 판정.
    design_cover_markers = re.findall(
        r"(?:WORKBOOK|PROJECT|Runner['’]?s\s*High|GOD\s*MIN|"
        r"\bTEST\s*[-–—]|TEST\s+\d|"
        r"신과\s*함께|객\s*[·‧・·]\s*서\s*최종\s*대비|"
        r"객서\s*심화|객\s*·\s*서|"
        r"최종\s*대비|중간고사\s*대비|기말고사\s*대비|"
        r"내신\s*대비|기출\s*통과|"
        r"문항편|해설편|정답편|"
        r"복습\s*과제|메인\s*자료|개념\s*완성)",
        full_text,
    )
    if len(design_cover_markers) >= 1 and len(full_text) < 300:
        return True

    # ── 시험지 헤더 페이지 감지 (수능/모의고사 표제) ──
    # "제 4교시 / 신민T 신념 모의고사 / 통합과학 N제 / 탐구 영역 / 홀수형" 같은 표제 페이지.
    exam_header_markers = re.findall(
        r"(?:제\s*\d+\s*교시|탐구\s*영역|홀수형|짝수형|"
        r"수능\s*모의고사|N제|모의고사\s*\d+회차?)",
        full_text,
    )
    # 표제 페이지는 본문 대비 텍스트가 아주 적음 (디자인+여백)
    if len(exam_header_markers) >= 2 and len(full_text) < 400:
        return True

    # 표지 페이지 감지: 시험지 메타정보는 있고 문항 지표는 없음.
    # "학년도 1학기 기말고사 과목명 성명" 류 조합이 표지의 특징.
    cover_markers = re.findall(
        r"(?:\d+학년도|\d+학기|기말\s?고사|중간\s?고사|과목\s?명|"
        r"문제지|답안지|답란\s?지|수험\s?번호|응시\s?번호|"
        r"성\s?명|학\s?번|반\s?번호)",
        full_text,
    )
    # 표지는 보통 본문 대비 매우 짧음. 500자 기준은 빈 박스+헤더 정도.
    if len(cover_markers) >= 2 and len(full_text) < 500:
        return True

    # ── 텍스트 매우 적은 페이지 (디자인/이미지 위주) ──
    # 문항 지시문도 없고 보기도 없는데 텍스트가 100자 미만이면 표지/디자인 페이지.
    # "1.", "2." 같은 번호만 있어서 anchor가 잡히는 표지 디자인 페이지 차단.
    if len(full_text) < 100:
        return True

    # 목차/차례 페이지 감지 — 키워드 + 페이지 번호 점선 패턴.
    # ".... 5", "··· 12" 같은 점선 가이드 또는 "목차/차례/Contents" 헤더.
    toc_keyword = bool(
        re.search(r"(?:^|\s)(?:목\s?차|차\s?례|Contents?|Table\s+of\s+Contents|INDEX)(?:\s|$)", full_text)
    )
    dot_leader_count = len(re.findall(r"[.·…]{3,}\s*\d{1,3}\b", full_text))
    if toc_keyword or dot_leader_count >= 4:
        # 문항 지표가 없을 때만 — 본문 페이지가 아니라는 확신
        if not has_choices and not has_question_indicator:
            return True

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


# 선택형(객관식) 문항 번호 패턴. "1.", "1) ", "(1) ", "[1] ", "문제 1.", "문 1)".
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

# 서술형/논술형/단답형 섹션은 1부터 번호를 리셋하므로 선택형 번호와 충돌함.
# 섹션별 number-space offset을 부여해 크로스-페이지 중복 제거 시 legit 문항이
# 잘못 드롭되지 않도록 한다.
# 예: `[서술형 1]` → 101, `[서술형 2]` → 102, `[논술형 1]` → 201.
# OCR은 "술"을 "답"으로 자주 오인식하므로 variant도 허용.
_SECTION_OFFSETS = {
    "서술": 100, "서답": 100,
    "논술": 200, "논답": 200,
    "단답": 300, "단술": 300,
    "약술": 400, "약답": 400,
}
_SECTION_PATTERN = re.compile(
    r"^\s*\[?\s*"
    r"(서\s*술|서\s*답|논\s*술|논\s*답|단\s*답|단\s*술|약\s*술|약\s*답)"
    r"\s*형?\s*(\d{1,3})"
)


def _extract_question_number(text: str) -> Optional[int]:
    """Extract question number from text block content.

    선택형 1~60 그대로. 서술형 N → 100+N. 논술형 N → 200+N. 단답형 N → 300+N.
    번호 공간을 분리해 서술형 리셋 번호가 선택형과 충돌하지 않게 한다.
    """
    text = text.strip()
    if not text:
        return None

    # 1. 서술형/논술형/단답형/약술형 섹션 패턴 먼저 검사
    sec_m = _SECTION_PATTERN.match(text)
    if sec_m:
        section_key = re.sub(r"\s+", "", sec_m.group(1))[:2]  # "서술" 등
        offset = _SECTION_OFFSETS.get(section_key, 0)
        try:
            sub_num = int(sec_m.group(2))
            if 1 <= sub_num <= _MAX_LEGIT_QUESTION_NUMBER:
                return offset + sub_num
        except ValueError:
            pass

    # 2. 선택형 번호 패턴
    m = _QUESTION_PATTERN.match(text)
    if not m:
        return None

    for g in m.groups():
        if g is not None:
            try:
                num = int(g)
                if 1 <= num <= _MAX_LEGIT_QUESTION_NUMBER:
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
    return right_count > len(blocks) * 0.2


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

    # 페이지 내 중복 번호 제거. 본문 내 "그림 4는..." 같은 표현이 regex와
    # 매치되는 경우, layout 순서상 먼저 나온 것을 실제 문항 앵커로 간주.
    seen: dict[int, int] = {}
    deduped: List[Tuple[int, int]] = []
    for qnum, idx in question_starts:
        if qnum in seen:
            continue
        seen[qnum] = idx
        deduped.append((qnum, idx))
    question_starts = deduped

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
        if i + 1 < len(question_starts):
            # 다음 문항 시작점
            next_block = sorted_blocks[question_starts[i + 1][1]]

            if is_dual_column:
                curr_in_left = start_block.x0 < mid_x
                next_in_left = next_block.x0 < mid_x
                if curr_in_left != next_in_left:
                    # 컬럼이 다르면 현재 컬럼 끝까지 (그림/표 포함)
                    y1 = page_height
                else:
                    # 같은 컬럼: 다음 문항 직전까지 전체 사용
                    # 그림/표가 텍스트 아래에 있으므로 gap 전체를 포함
                    y1 = next_block.y0 - margin
            else:
                # 단일 컬럼: 다음 문항 직전까지 전체 사용
                y1 = next_block.y0 - margin
        else:
            # 마지막 문항: 페이지 하단까지 (비텍스트 요소 포함)
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


def validate_anchors_across_pages(
    regions_per_page: List[List[QuestionRegion]],
) -> List[List[QuestionRegion]]:
    """
    여러 페이지의 anchor를 모아 문서 전역 검증.

    드롭되는 패턴:
      1. 크로스-페이지 중복 — 동일 번호가 여러 페이지에 등장하면
         layout 순서상 가장 앞선 페이지의 것만 유지. 후속 페이지의
         중복은 본문 내 "그림 4는", "표 2에" 등으로 오탐된 것.
      2. 시퀀스 outlier — sorted unique 번호의 최대 gap이 비정상적으로
         크면(median gap 대비 >= 5배 & 절대값 >= 5) 이탈값을 제거.
         예: [3, 4, 5, 6, 7, 46] → 46 드롭.

    입력 형식: [per_page_regions]
    반환: 필터링된 [per_page_regions] (페이지 구조 유지, 내부 regions만 필터)
    """
    if not regions_per_page:
        return regions_per_page

    # ── 1. 크로스-페이지 중복 제거 ──
    seen_numbers: set[int] = set()
    filtered: List[List[QuestionRegion]] = []
    for page_regions in regions_per_page:
        kept: List[QuestionRegion] = []
        for r in page_regions:
            if r.number in seen_numbers:
                continue
            seen_numbers.add(r.number)
            kept.append(r)
        filtered.append(kept)

    # ── 2. 시퀀스 outlier 제거 (선택형/서술형/논술형 각 number-space별 개별 적용) ──
    # number-space는 100 단위로 분리 (선택형 <100, 서술형 100~199, 논술형 200~299 등).
    outlier_nums: set[int] = set()
    by_space: dict[int, List[int]] = {}
    for n in sorted(seen_numbers):
        space = n // 100
        by_space.setdefault(space, []).append(n)

    for space_nums in by_space.values():
        if len(space_nums) < 4:
            continue
        gaps = [space_nums[i + 1] - space_nums[i] for i in range(len(space_nums) - 1)]
        sorted_gaps = sorted(gaps)
        median_gap = sorted_gaps[len(sorted_gaps) // 2]
        for i, gap in enumerate(gaps):
            if gap >= 5 and gap >= median_gap * 5:
                # 이탈값 이후 전부 outlier로 처리 (연속 이탈 가능성).
                outlier_nums.update(space_nums[i + 1:])
                break

    if outlier_nums:
        filtered = [
            [r for r in page_regions if r.number not in outlier_nums]
            for page_regions in filtered
        ]

    return filtered
