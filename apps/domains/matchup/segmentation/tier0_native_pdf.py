"""Stage 5 Tier 0 — Native PDF parser prototype (2026-05-06).

born-digital PDF 의 text/word/image block 추출 + 문제 번호 anchor 후보 + bbox 후보.

원칙 (사용자 directive):
- 운영 DB 어떤 변경도 X — 순수 함수 모음.
- 운영 코드와 분리: 기존 dispatcher / segment_yolo / vlm_fallback 미import.
- 프로토타입: 결과는 list[dict] 반환 (artifact JSON 으로 dump 가능).
- bbox 좌표는 PDF point (1/72 inch) — 픽셀 변환은 호출자 책임.

의존성: PyMuPDF (fitz) — 기존 backend 운영 의존성.

flow:
    extract_page_blocks(pdf_path) → per-page (text_blocks + word_blocks + image_blocks)
    detect_problem_anchors(blocks) → 문제 번호 ([1, 2, 3, ...] 또는 [⑴, ⑵, ...]) 위치
    derive_bbox_candidates(anchors, page_dim) → 인접 anchor 사이의 영역을 problem bbox 로 추정
    classify_page_role(blocks, page_dim) → cover / index / problem / explanation / answer_key
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any, Optional

# 문제 번호 anchor 정규식 — 다양한 시험지 양식 대응.
# 예: "1.", "1)", "1번", "(1)", "①" "⑴" "Ⅰ" 등.
_NUM_ARABIC_DOT = re.compile(r"^\s*(\d{1,3})\s*\.\s*$")        # "1." "12."
_NUM_ARABIC_PAREN = re.compile(r"^\s*(\d{1,3})\s*[\)\]]\s*$")   # "1)" "1]"
_NUM_ARABIC_BUNG = re.compile(r"^\s*(\d{1,3})\s*번\s*$")       # "1번"
_NUM_ARABIC_INPAREN = re.compile(r"^\s*\(\s*(\d{1,3})\s*\)\s*$")  # "(1)"

# 시작이 숫자로 시작하는 짧은 줄 (예: "1. 다음 그림은 ...").
_NUM_LINE_START = re.compile(r"^\s*(\d{1,3})\s*[\.\)]\s+")

# circled digit ① ~ ⑳
_CIRCLED = {chr(0x2460 + i): i + 1 for i in range(20)}
# parenthesized digit ⑴ ~ ⒇
_PARENED = {chr(0x2474 + i): i + 1 for i in range(20)}

# non-question paper 인 강력한 키워드 (cover/index/answer_key 등).
_NON_QUESTION_HINTS = {
    "cover": ["표지", "시험지", "고사", "modified test"],
    "answer_key": ["정답", "해설", "answer key"],
    "index": ["목차", "차례"],
}


@dataclass
class NumberAnchor:
    """문제 번호 anchor 1개."""
    number: int
    page_index: int
    bbox: tuple[float, float, float, float]  # (x0, y0, x1, y1) PDF points
    text: str
    style: str  # "arabic_dot" / "arabic_paren" / "circled" / "parened" / "line_start"
    confidence: float  # 0.0 ~ 1.0


@dataclass
class BboxCandidate:
    """anchor 사이 영역에서 추정한 problem bbox."""
    number: int
    page_index: int
    bbox: tuple[float, float, float, float]  # (x0, y0, x1, y1) PDF points
    bbox_norm: tuple[float, float, float, float]  # (x, y, w, h) 0~1 normalized
    text_preview: str = ""
    confidence: float = 0.0


@dataclass
class PageBlocks:
    """page 1개의 raw 추출 결과."""
    page_index: int
    page_width: float    # PDF points
    page_height: float
    has_embedded_text: bool
    text_blocks: list[dict] = field(default_factory=list)   # {x0, y0, x1, y1, text}
    word_blocks: list[dict] = field(default_factory=list)   # {x0, y0, x1, y1, text}
    image_blocks: list[dict] = field(default_factory=list)  # {x0, y0, x1, y1}


@dataclass
class PageRoleAnalysis:
    """page 의 페이지 역할 추정 (cover / problem / answer_key / index / unknown)."""
    page_index: int
    role: str  # "cover" | "problem" | "answer_key" | "index" | "unknown"
    confidence: float
    debug: dict = field(default_factory=dict)


def extract_page_blocks(pdf_path: str) -> list[PageBlocks]:
    """PDF 의 모든 페이지에서 text/word/image 블록 추출.

    born-digital PDF 만 의미 있음 — 스캔본은 has_embedded_text=False 로 표시.

    Args:
        pdf_path: PDF 파일 경로

    Returns:
        per-page PageBlocks 리스트.
    """
    import fitz  # PyMuPDF

    pages: list[PageBlocks] = []
    with fitz.open(pdf_path) as doc:
        for page_index, page in enumerate(doc):
            rect = page.rect
            page_width = float(rect.width)
            page_height = float(rect.height)

            text = page.get_text("text") or ""
            has_embedded_text = bool(text.strip())

            text_blocks: list[dict] = []
            word_blocks: list[dict] = []
            image_blocks: list[dict] = []

            if has_embedded_text:
                # text blocks — paragraph-level
                for block in page.get_text("blocks"):
                    if len(block) < 5:
                        continue
                    x0, y0, x1, y1, btext = block[:5]
                    text_blocks.append({
                        "x0": float(x0), "y0": float(y0),
                        "x1": float(x1), "y1": float(y1),
                        "text": str(btext).strip(),
                    })

                # word blocks — fine-grained for anchor detection
                for word in page.get_text("words"):
                    if len(word) < 5:
                        continue
                    x0, y0, x1, y1, wtext = word[:5]
                    word_blocks.append({
                        "x0": float(x0), "y0": float(y0),
                        "x1": float(x1), "y1": float(y1),
                        "text": str(wtext).strip(),
                    })

            # image blocks — 이미지 위치 추출
            try:
                for img_dict in page.get_image_info():
                    bbox = img_dict.get("bbox")
                    if bbox and len(bbox) >= 4:
                        image_blocks.append({
                            "x0": float(bbox[0]), "y0": float(bbox[1]),
                            "x1": float(bbox[2]), "y1": float(bbox[3]),
                        })
            except Exception:
                # 일부 PDF 는 image_info 가 없거나 형식이 다름 — skip.
                pass

            pages.append(PageBlocks(
                page_index=page_index,
                page_width=page_width,
                page_height=page_height,
                has_embedded_text=has_embedded_text,
                text_blocks=text_blocks,
                word_blocks=word_blocks,
                image_blocks=image_blocks,
            ))

    return pages


def detect_problem_anchors(page: PageBlocks) -> list[NumberAnchor]:
    """page 의 word/text 블록에서 문제 번호 anchor 후보 추출.

    여러 패턴을 동시에 검사하되, 같은 위치에 중복 매칭되면 confidence 가 높은 쪽 선택.

    원칙:
    - 단일 word 가 "1." "(1)" "①" 형태면 anchor 후보.
    - text_blocks 첫 줄이 "1. xxx" 시작이면 anchor 후보 (line_start).
    - confidence 는 패턴 + position (페이지 좌측 ↑) 기반 휴리스틱.
    """
    anchors: list[NumberAnchor] = []

    # 1. word_blocks 검사
    for w in page.word_blocks:
        text = (w.get("text") or "").strip()
        if not text:
            continue
        match_n: Optional[int] = None
        match_style: Optional[str] = None
        match_conf = 0.0

        # circled / parened
        if text in _CIRCLED:
            match_n = _CIRCLED[text]
            match_style = "circled"
            match_conf = 0.9
        elif text in _PARENED:
            match_n = _PARENED[text]
            match_style = "parened"
            match_conf = 0.9
        # 정규식
        elif (m := _NUM_ARABIC_DOT.match(text)):
            match_n = int(m.group(1))
            match_style = "arabic_dot"
            match_conf = 0.85
        elif (m := _NUM_ARABIC_PAREN.match(text)):
            match_n = int(m.group(1))
            match_style = "arabic_paren"
            match_conf = 0.8
        elif (m := _NUM_ARABIC_BUNG.match(text)):
            match_n = int(m.group(1))
            match_style = "arabic_bung"
            match_conf = 0.8
        elif (m := _NUM_ARABIC_INPAREN.match(text)):
            match_n = int(m.group(1))
            match_style = "arabic_inparen"
            match_conf = 0.75

        if match_n is not None and 1 <= match_n <= 200:
            # 좌측 marker bonus (페이지 너비의 좌측 30% 안)
            if w["x0"] < page.page_width * 0.3:
                match_conf = min(1.0, match_conf + 0.05)

            anchors.append(NumberAnchor(
                number=match_n,
                page_index=page.page_index,
                bbox=(w["x0"], w["y0"], w["x1"], w["y1"]),
                text=text,
                style=match_style,
                confidence=match_conf,
            ))

    # 2. text_blocks 첫 줄 line_start (word_blocks 못 잡은 경우 보조)
    for tb in page.text_blocks:
        text = (tb.get("text") or "")
        first_line = text.splitlines()[0] if text else ""
        m = _NUM_LINE_START.match(first_line)
        if not m:
            continue
        n = int(m.group(1))
        if not (1 <= n <= 200):
            continue
        # 이미 word_blocks 에서 잡은 같은 anchor 면 skip (중복 방지 — y0 ±5 pt 안)
        already = any(
            abs(a.bbox[1] - tb["y0"]) < 5 and a.number == n
            for a in anchors
        )
        if already:
            continue
        anchors.append(NumberAnchor(
            number=n,
            page_index=page.page_index,
            bbox=(tb["x0"], tb["y0"], tb["x0"] + 30.0, tb["y0"] + 12.0),  # 추정 marker 영역
            text=first_line[:30],
            style="line_start",
            confidence=0.7,
        ))

    # 정렬: y0 ↑, x0 ↑ (top-down, left-right)
    anchors.sort(key=lambda a: (a.bbox[1], a.bbox[0]))
    return anchors


def derive_bbox_candidates(
    anchors: list[NumberAnchor],
    page: PageBlocks,
) -> list[BboxCandidate]:
    """anchor 사이의 vertical 간격을 problem bbox 로 추정.

    아주 단순한 heuristic — 운영용 splitter (academy.domain.tools.question_splitter) 가
    훨씬 정교함. 본 prototype 은 dispatcher 흐름 검증용.

    Algorithm:
    - 같은 페이지 anchor 들을 y0 순으로 정렬.
    - i 번째 anchor 의 bbox = (margin_x0, anchor[i].y0, page_w - margin_x0, next_anchor.y0 또는 page_h - margin)
    - bbox_norm 으로 0~1 변환.
    """
    if not anchors:
        return []

    sorted_anchors = sorted(anchors, key=lambda a: a.bbox[1])
    candidates: list[BboxCandidate] = []
    page_w = page.page_width
    page_h = page.page_height

    margin_x = page_w * 0.05  # 좌우 5% margin

    for i, anchor in enumerate(sorted_anchors):
        x0 = margin_x
        y0 = anchor.bbox[1]
        x1 = page_w - margin_x

        if i + 1 < len(sorted_anchors):
            y1 = sorted_anchors[i + 1].bbox[1]
        else:
            y1 = page_h - (page_h * 0.05)

        # text_preview — anchor 이후 같은 영역 안에 있는 word 들 합치기 (앞 80자)
        preview_words = []
        for w in page.word_blocks:
            if (
                w["y0"] >= y0 and w["y1"] <= y1
                and w["x0"] >= x0 and w["x1"] <= x1
            ):
                preview_words.append(w.get("text", ""))
        text_preview = " ".join(preview_words)[:80]

        # confidence: anchor 자체 conf + 영역 내 word 수 보정
        conf = anchor.confidence
        if len(preview_words) >= 5:
            conf = min(1.0, conf + 0.05)

        bbox_norm = (
            x0 / page_w,
            y0 / page_h,
            (x1 - x0) / page_w,
            (y1 - y0) / page_h,
        )
        candidates.append(BboxCandidate(
            number=anchor.number,
            page_index=anchor.page_index,
            bbox=(x0, y0, x1, y1),
            bbox_norm=bbox_norm,
            text_preview=text_preview,
            confidence=conf,
        ))

    return candidates


def classify_page_role(page: PageBlocks) -> PageRoleAnalysis:
    """page 의 역할을 키워드/구조 휴리스틱으로 추정.

    Stage 5.0 prototype — 정교한 분류기는 academy.domain.tools.paper_type 사용.
    """
    if not page.has_embedded_text:
        return PageRoleAnalysis(
            page_index=page.page_index,
            role="unknown",
            confidence=0.0,
            debug={"reason": "no_embedded_text"},
        )

    all_text = " ".join(b.get("text", "") for b in page.text_blocks).lower()

    for role, keywords in _NON_QUESTION_HINTS.items():
        for kw in keywords:
            if kw.lower() in all_text:
                return PageRoleAnalysis(
                    page_index=page.page_index,
                    role=role,
                    confidence=0.7,
                    debug={"matched_keyword": kw},
                )

    # 기본: anchor 가 1개 이상이면 problem, 없으면 unknown
    anchors = detect_problem_anchors(page)
    if len(anchors) >= 2:
        return PageRoleAnalysis(
            page_index=page.page_index,
            role="problem",
            confidence=0.8,
            debug={"anchor_count": len(anchors)},
        )

    return PageRoleAnalysis(
        page_index=page.page_index,
        role="unknown",
        confidence=0.3,
        debug={"anchor_count": len(anchors)},
    )


def analyze_pdf(pdf_path: str) -> dict[str, Any]:
    """단일 PDF 의 모든 페이지 분석 — 전체 흐름 통합.

    Returns:
        {
            "pdf_path": str,
            "page_count": int,
            "pages": [
                {
                    "page_index": int,
                    "page_width": float, "page_height": float,
                    "has_embedded_text": bool,
                    "role": str, "role_confidence": float,
                    "anchor_count": int,
                    "anchors": [...],
                    "bbox_candidates": [...],
                },
                ...
            ],
        }
    """
    pages = extract_page_blocks(pdf_path)
    out = {
        "pdf_path": pdf_path,
        "page_count": len(pages),
        "pages": [],
    }
    for page in pages:
        role = classify_page_role(page)
        anchors = detect_problem_anchors(page)
        candidates = derive_bbox_candidates(anchors, page)
        out["pages"].append({
            "page_index": page.page_index,
            "page_width": page.page_width,
            "page_height": page.page_height,
            "has_embedded_text": page.has_embedded_text,
            "role": role.role,
            "role_confidence": role.confidence,
            "role_debug": role.debug,
            "text_block_count": len(page.text_blocks),
            "word_block_count": len(page.word_blocks),
            "image_block_count": len(page.image_blocks),
            "anchor_count": len(anchors),
            "anchors": [asdict(a) for a in anchors],
            "bbox_candidates": [asdict(c) for c in candidates],
        })
    return out


# ════════════════════════════════════════════════════════════════════════════
# Stage 5.2 v2 — Tier 0 정밀화 (anchor over-detection 10x → ~1x 목표)
# ════════════════════════════════════════════════════════════════════════════
#
# v1 한계 (Stage 5.1 평가):
#  1. anchor over-detection 10x — 본문 inline "1." "2." 모두 anchor 인식
#  2. page_role false positive — "기출"/"고사" 키워드가 페이지 어디서나 매칭
#  3. 정답표/해설 페이지 anchor 폭증 ("1.④ 2.③ 3.①" 60+ 반복)
#  4. column awareness 없음 — 좌측/우측 column 혼동
#  5. cross-page sequence validation 없음
#
# v2 보강 (운영 question_splitter / paper_type 일부 휴리스틱 흡수):
#  1. line_start strict — anchor word 가 같은 y-band 의 leftmost word 여야 함
#  2. _MAX_LEGIT_QUESTION_NUMBER=60 (운영 정의 흡수)
#  3. 정답표/해설/zb 마커 페이지 차단 (anchor=0 처리)
#  4. column detection (1/2/4 columns by x0 histogram)
#  5. column 시작 x0 근접도 필터
#  6. cross-page sequence continuity 검증
#  7. page_role 키워드 검사 영역을 페이지 첫 200자 또는 상단 25%로 제한
#  8. anchor 충분 + sequence 정상 → role=problem 우선
#  9. text_pages=0 → tier1_required 명시 분류

# 운영 정의 흡수 — 시험 문항 번호 현실 상한
_MAX_LEGIT_QUESTION_NUMBER_V2 = 60

# 정답표 / 해설지 / zb 마커 패턴 (운영 question_splitter.is_non_question_page 흡수)
_ANSWER_TABLE_RE = re.compile(r"\b\d{1,3}\.\s*[①②③④⑤]")
_EXPLANATION_RE = re.compile(r"\b\d{1,3}\s*\.\s*(?:정\s*답|문제\s*해설)")
_STANDALONE_ANSWER_RE = re.compile(r"정\s*답\s*[①②③④⑤]")
_ZB_MARKER_RE = re.compile(r"\bzb\s*\d{1,3}\s*\)")
_QUESTION_INDICATOR_KW = (
    "옳은 것", "구하시오", "표시하시오", "고르시오", "서술하시오",
    "풀이 과정", "이에 대한 설명", "다음 중", "보기에서",
)


@dataclass
class ColumnLayout:
    """페이지의 column 추정 결과."""
    column_count: int  # 1 / 2 / 4
    column_lefts: list[float]  # column 별 left edge x0 (PDF points)
    column_width: float  # 각 column 평균 width


@dataclass
class CrossPageValidation:
    """문서 전체 cross-page anchor sequence validation 결과."""
    detected_total: int
    expected_max: int  # 도달한 anchor 번호의 max
    sequence_continuity: float  # 0~1: 누락 없는 정도
    duplicates_dropped: int
    suspicious_pages: list[int]


def _is_line_leading_word(
    word: dict, all_words: list[dict], *, y_tol: float = 3.0, x_tol: float = 5.0,
) -> bool:
    """word 가 같은 y-band 안 leftmost word 인지 (line_start strict).

    line 의 leading word 가 anchor 후보. 본문 inline "1." 같은 false positive 차단.
    """
    same_line = [
        w for w in all_words
        if abs(w["y0"] - word["y0"]) < y_tol
    ]
    if not same_line:
        return False
    leftmost_x = min(w["x0"] for w in same_line)
    return word["x0"] - leftmost_x < x_tol


def _is_answer_or_explanation_page(page: PageBlocks) -> tuple[bool, str]:
    """운영 question_splitter.is_non_question_page 의 핵심 차단 패턴 흡수.

    정답표 ("1.④ 2.③" 5+) / 해설지 ("N. 정답") / standalone "정답 ④" / zb 마커
    페이지는 anchor 폭증 주범 — 명시 차단.

    return: (차단 여부, 차단 사유)
    """
    full_text = " ".join(b.get("text", "") for b in page.text_blocks).strip()
    if not full_text:
        return (False, "")

    # 정답표
    if len(_ANSWER_TABLE_RE.findall(full_text)) >= 5:
        if not any(kw in full_text for kw in _QUESTION_INDICATOR_KW):
            return (True, "answer_table")

    # 해설지 ("N. 정답" 3+)
    if len(_EXPLANATION_RE.findall(full_text)) >= 3:
        return (True, "explanation_page")

    # standalone "정답 ④" 3+
    if len(_STANDALONE_ANSWER_RE.findall(full_text)) >= 3:
        return (True, "standalone_answer")

    # zb 마커 3+ (학습자료 본문 항목번호)
    if len(_ZB_MARKER_RE.findall(full_text)) >= 3:
        return (True, "zb_markers")

    return (False, "")


def detect_columns(
    word_blocks: list[dict], page_width: float,
    *, min_words_per_column: int = 5,
) -> ColumnLayout:
    """word x0 분포로 column 개수 + left edges 추정.

    1단/2단/4단 양식 검출. 단순 1D histogram + cluster.

    Returns:
        ColumnLayout(column_count, column_lefts, column_width).
        word 부족 시 (column_count=1, [0.0], page_width).
    """
    if len(word_blocks) < min_words_per_column:
        return ColumnLayout(column_count=1, column_lefts=[0.0], column_width=page_width)

    # x0 histogram — page_width 를 32 bin 으로
    bin_count = 32
    bin_w = page_width / bin_count
    histogram = [0] * bin_count
    for w in word_blocks:
        b = min(int(w["x0"] / bin_w), bin_count - 1)
        if b >= 0:
            histogram[b] += 1

    # peak 검출 — 인접 bin 의 local maxima
    peaks = []
    for i in range(bin_count):
        if histogram[i] < min_words_per_column:
            continue
        is_peak = True
        for j in (i - 1, i + 1):
            if 0 <= j < bin_count and histogram[j] > histogram[i]:
                is_peak = False
                break
        if is_peak:
            peaks.append((i, histogram[i]))

    # peak count 가 1/2/4 와 가까운지
    n_peaks = len(peaks)
    if n_peaks <= 1:
        return ColumnLayout(column_count=1, column_lefts=[0.0], column_width=page_width)

    # 너무 많은 peak (column 와 무관한 word density variance) — 보수적 1단 처리
    if n_peaks > 4:
        # 최대 2개만 picking (가장 큰 peak 2개)
        peaks.sort(key=lambda p: p[1], reverse=True)
        picks = sorted([p[0] for p in peaks[:2]])
        if len(picks) == 2 and picks[1] - picks[0] >= bin_count // 4:
            lefts = [picks[0] * bin_w, picks[1] * bin_w]
            cw = page_width / 2
            return ColumnLayout(column_count=2, column_lefts=lefts, column_width=cw)
        return ColumnLayout(column_count=1, column_lefts=[0.0], column_width=page_width)

    # 2/3/4 peaks
    sorted_lefts = sorted(p[0] * bin_w for p in peaks)
    cw = page_width / n_peaks
    if n_peaks == 2:
        return ColumnLayout(column_count=2, column_lefts=sorted_lefts, column_width=cw)
    if n_peaks in (3, 4):
        # 3은 4 column 중 1개 누락 가능성 — 4로 추정 (보수적), 또는 2 로 fallback
        if n_peaks == 4:
            return ColumnLayout(column_count=4, column_lefts=sorted_lefts, column_width=cw)
        # n_peaks=3 → 2 column으로 보수적 fallback
        return ColumnLayout(column_count=2, column_lefts=sorted_lefts[:2], column_width=page_width / 2)

    return ColumnLayout(column_count=1, column_lefts=[0.0], column_width=page_width)


def detect_problem_anchors_v2(page: PageBlocks, columns: ColumnLayout) -> list[NumberAnchor]:
    """v2 anchor 검출 — line_start strict + _MAX_LEGIT_NUMBER + column 근접도.

    v1 한계 fix:
    - 본문 inline "1." 차단 (line_leading word 만)
    - 번호 ≤ 60 (운영 _MAX_LEGIT_QUESTION_NUMBER 흡수)
    - column 시작 x0 근접 word 만 picking
    """
    # 운영 차단 패턴 — 정답표/해설/zb 페이지면 anchor=[]
    blocked, reason = _is_answer_or_explanation_page(page)
    if blocked:
        return []

    anchors: list[NumberAnchor] = []
    all_words = page.word_blocks

    # column 시작 x0 set (tolerance 0.05 * page_width)
    col_tol = page.page_width * 0.05
    column_starts = columns.column_lefts

    for w in all_words:
        text = (w.get("text") or "").strip()
        if not text:
            continue

        match_n: Optional[int] = None
        match_style: Optional[str] = None
        match_conf = 0.0

        if text in _CIRCLED:
            match_n = _CIRCLED[text]
            match_style = "circled"
            match_conf = 0.9
        elif text in _PARENED:
            match_n = _PARENED[text]
            match_style = "parened"
            match_conf = 0.9
        elif (m := _NUM_ARABIC_DOT.match(text)):
            match_n = int(m.group(1))
            match_style = "arabic_dot"
            match_conf = 0.85
        elif (m := _NUM_ARABIC_PAREN.match(text)):
            match_n = int(m.group(1))
            match_style = "arabic_paren"
            match_conf = 0.8
        elif (m := _NUM_ARABIC_BUNG.match(text)):
            match_n = int(m.group(1))
            match_style = "arabic_bung"
            match_conf = 0.8
        elif (m := _NUM_ARABIC_INPAREN.match(text)):
            match_n = int(m.group(1))
            match_style = "arabic_inparen"
            match_conf = 0.75

        if match_n is None:
            continue

        # v2 핵심 fix #1 — _MAX_LEGIT_QUESTION_NUMBER 운영 정의 흡수
        if not (1 <= match_n <= _MAX_LEGIT_QUESTION_NUMBER_V2):
            continue

        # v2 핵심 fix #2 — line_start strict
        if not _is_line_leading_word(w, all_words):
            continue

        # v2 핵심 fix #3 — column 시작 x0 근접도
        is_at_column_start = any(
            abs(w["x0"] - col_x) < col_tol for col_x in column_starts
        )
        if not is_at_column_start:
            # 1단 layout 이고 좌측 50% 안이면 통과 (보수적 양보)
            if columns.column_count == 1 and w["x0"] < page.page_width * 0.5:
                pass
            else:
                continue

        # confidence 보정: column start bonus
        if is_at_column_start:
            match_conf = min(1.0, match_conf + 0.05)

        anchors.append(NumberAnchor(
            number=match_n,
            page_index=page.page_index,
            bbox=(w["x0"], w["y0"], w["x1"], w["y1"]),
            text=text,
            style=match_style,
            confidence=match_conf,
        ))

    # v2 핵심 fix #4 — 같은 페이지 안 같은 number 중복 dedup (가장 conf 높은 것만)
    by_number: dict[int, NumberAnchor] = {}
    for a in anchors:
        prev = by_number.get(a.number)
        if not prev or a.confidence > prev.confidence:
            by_number[a.number] = a
    deduped = list(by_number.values())
    deduped.sort(key=lambda a: (a.bbox[1], a.bbox[0]))
    return deduped


def cross_page_validate(
    per_page_anchors: list[list[NumberAnchor]],
    *, expected_max: Optional[int] = None,
) -> CrossPageValidation:
    """문서 전체 anchor sequence 검증.

    - 페이지별 anchor 의 number 가 전체적으로 monotonically increase 인지
    - 중복 number 가 페이지 간 발생하면 의심 페이지 표시
    - expected_max (페이지 단위 예상 최대) 비교
    """
    seen_numbers: dict[int, int] = {}  # number → 처음 발견한 page_index
    suspicious: set[int] = set()
    duplicates_dropped = 0

    for page_idx, anchors in enumerate(per_page_anchors):
        for a in anchors:
            if a.number in seen_numbers and seen_numbers[a.number] != page_idx:
                # 다른 페이지에서 같은 번호 — 의심
                suspicious.add(page_idx)
                duplicates_dropped += 1
            else:
                seen_numbers[a.number] = page_idx

    detected_total = sum(len(a) for a in per_page_anchors)
    detected_max = max(seen_numbers.keys()) if seen_numbers else 0

    # sequence continuity: 1..detected_max 중 보유율
    if detected_max > 0:
        present = sum(1 for n in range(1, detected_max + 1) if n in seen_numbers)
        continuity = present / detected_max
    else:
        continuity = 0.0

    return CrossPageValidation(
        detected_total=detected_total,
        expected_max=detected_max,
        sequence_continuity=continuity,
        duplicates_dropped=duplicates_dropped,
        suspicious_pages=sorted(suspicious),
    )


def classify_page_role_v2(
    page: PageBlocks, anchors: list[NumberAnchor],
) -> PageRoleAnalysis:
    """v2 page role — 키워드 검사 영역 제한 + anchor 우선.

    v1 결함 fix:
    - "기출", "고사" 같은 키워드가 페이지 어디서나 cover false positive 만들었음 →
      페이지 첫 200자 또는 상단 25% 영역에서만 검사.
    - anchor 가 충분히 있고 sequence 가 정상이면 role=problem 우선.
    - 정답표/해설지는 _is_answer_or_explanation_page 로 별도 차단.
    """
    if not page.has_embedded_text:
        return PageRoleAnalysis(
            page_index=page.page_index, role="unknown",
            confidence=0.0, debug={"reason": "no_embedded_text"},
        )

    # 정답/해설/zb 마커 우선 차단
    blocked, reason = _is_answer_or_explanation_page(page)
    if blocked:
        return PageRoleAnalysis(
            page_index=page.page_index,
            role="answer_key" if reason in ("answer_table", "explanation_page", "standalone_answer") else "unknown",
            confidence=0.85,
            debug={"reason": reason},
        )

    # v2 fix — 키워드 검사 영역 제한:
    # 페이지 상단 25% 내 text_block 의 첫 200자 + 모든 text_block 의 첫 줄.
    top_y = page.page_height * 0.25
    top_text_blocks = [b for b in page.text_blocks if b.get("y0", 0) < top_y]
    top_text = " ".join(b.get("text", "") for b in top_text_blocks)[:300]
    first_line_text = " ".join(
        (b.get("text", "") or "").split("\n", 1)[0] for b in page.text_blocks
    )[:200]
    keyword_search = top_text + " " + first_line_text

    for role, keywords in _NON_QUESTION_HINTS.items():
        for kw in keywords:
            if kw.lower() in keyword_search.lower():
                # anchor 가 충분 (page 평균 이상) 이면 problem 으로 판단 우선 — false positive 방지
                if len(anchors) >= 5:
                    continue
                return PageRoleAnalysis(
                    page_index=page.page_index, role=role,
                    confidence=0.7,
                    debug={"matched_keyword": kw, "scope": "top_or_first_line"},
                )

    if len(anchors) >= 2:
        # sequence continuity 보너스
        nums = sorted(a.number for a in anchors)
        gaps = sum(1 for i in range(1, len(nums)) if nums[i] - nums[i - 1] != 1)
        conf = 0.85 if gaps == 0 else 0.75
        return PageRoleAnalysis(
            page_index=page.page_index, role="problem",
            confidence=conf,
            debug={"anchor_count": len(anchors), "sequence_gaps": gaps},
        )

    return PageRoleAnalysis(
        page_index=page.page_index, role="unknown",
        confidence=0.3, debug={"anchor_count": len(anchors)},
    )


def derive_bbox_candidates_v2(
    anchors: list[NumberAnchor], page: PageBlocks, columns: ColumnLayout,
) -> list[BboxCandidate]:
    """v2 bbox 도출 — column-aware.

    column_count >= 2 면 각 column 별로 sequence 분리해서 bbox 생성 — 좌측 column
    의 마지막 problem 이 우측 column 의 첫 problem 까지 이어지지 않도록.
    """
    if not anchors:
        return []

    if columns.column_count <= 1:
        return derive_bbox_candidates(anchors, page)  # v1 fallback (1단)

    # column 별 anchor 분리 — anchor.bbox.x0 가 어느 column 에 가까운지
    cols_sorted = sorted(columns.column_lefts)
    col_anchors: list[list[NumberAnchor]] = [[] for _ in cols_sorted]
    for a in anchors:
        # 각 column left 까지 거리 계산
        ax = a.bbox[0]
        nearest = min(range(len(cols_sorted)), key=lambda i: abs(ax - cols_sorted[i]))
        col_anchors[nearest].append(a)

    candidates: list[BboxCandidate] = []
    page_w = page.page_width
    page_h = page.page_height

    for col_idx, anchors_in_col in enumerate(col_anchors):
        if not anchors_in_col:
            continue
        col_left = cols_sorted[col_idx]
        col_right = (
            cols_sorted[col_idx + 1]
            if col_idx + 1 < len(cols_sorted)
            else page_w * 0.95
        )
        sorted_a = sorted(anchors_in_col, key=lambda a: a.bbox[1])

        for i, anchor in enumerate(sorted_a):
            x0 = max(col_left, page_w * 0.02)
            y0 = anchor.bbox[1]
            x1 = min(col_right - page_w * 0.01, page_w * 0.98)

            if i + 1 < len(sorted_a):
                y1 = sorted_a[i + 1].bbox[1]
            else:
                y1 = page_h * 0.95

            preview_words = [
                w.get("text", "")
                for w in page.word_blocks
                if (
                    w["y0"] >= y0 and w["y1"] <= y1
                    and w["x0"] >= x0 and w["x1"] <= x1
                )
            ]
            text_preview = " ".join(preview_words)[:80]

            bbox_norm = (
                x0 / page_w,
                y0 / page_h,
                (x1 - x0) / page_w,
                (y1 - y0) / page_h,
            )
            candidates.append(BboxCandidate(
                number=anchor.number,
                page_index=anchor.page_index,
                bbox=(x0, y0, x1, y1),
                bbox_norm=bbox_norm,
                text_preview=text_preview,
                confidence=anchor.confidence,
            ))
    return candidates


def analyze_pdf_v2(pdf_path: str) -> dict[str, Any]:
    """v2 통합 entrypoint — Tier 0 정밀화 + Tier 1 후보 분류.

    Returns:
        {
            "version": "v2",
            "pdf_path": str,
            "page_count": int,
            "tier1_required": bool,        # text_pages=0 → Tier 1 OCR 필수
            "tier1_reason": str,
            "cross_page": {detected_total, expected_max, sequence_continuity, ...},
            "pages": [
                {... v1 키 + columns + role_v2 ...},
                ...
            ],
        }
    """
    pages = extract_page_blocks(pdf_path)
    n_pages = len(pages)
    n_text_pages = sum(1 for p in pages if p.has_embedded_text)

    # Tier 1 필요 여부 — born-digital 인지
    tier1_required = False
    tier1_reason = ""
    if n_pages == 0:
        tier1_required = True
        tier1_reason = "empty_pdf"
    elif n_text_pages == 0:
        tier1_required = True
        tier1_reason = "scanned_no_text_layer"
    elif n_text_pages < n_pages * 0.5:
        tier1_required = True
        tier1_reason = "partial_text_layer"

    out: dict[str, Any] = {
        "version": "v2",
        "pdf_path": pdf_path,
        "page_count": n_pages,
        "text_pages": n_text_pages,
        "tier1_required": tier1_required,
        "tier1_reason": tier1_reason,
        "pages": [],
    }

    # 페이지별 분석
    per_page_anchors: list[list[NumberAnchor]] = []
    per_page_columns: list[ColumnLayout] = []
    for page in pages:
        cols = detect_columns(page.word_blocks, page.page_width)
        anchors = detect_problem_anchors_v2(page, cols)
        per_page_anchors.append(anchors)
        per_page_columns.append(cols)

    # cross-page validation
    cross = cross_page_validate(per_page_anchors)

    # 페이지별 결과 dump
    for page, anchors, cols in zip(pages, per_page_anchors, per_page_columns):
        role = classify_page_role_v2(page, anchors)
        candidates = derive_bbox_candidates_v2(anchors, page, cols)
        suspicious = page.page_index in cross.suspicious_pages
        out["pages"].append({
            "page_index": page.page_index,
            "page_width": page.page_width,
            "page_height": page.page_height,
            "has_embedded_text": page.has_embedded_text,
            "role": role.role,
            "role_confidence": role.confidence,
            "role_debug": role.debug,
            "text_block_count": len(page.text_blocks),
            "word_block_count": len(page.word_blocks),
            "image_block_count": len(page.image_blocks),
            "anchor_count": len(anchors),
            "anchors": [asdict(a) for a in anchors],
            "bbox_candidates": [asdict(c) for c in candidates],
            "columns": {
                "count": cols.column_count,
                "lefts": cols.column_lefts,
                "width": cols.column_width,
            },
            "suspicious": suspicious,
        })

    out["cross_page"] = {
        "detected_total": cross.detected_total,
        "expected_max": cross.expected_max,
        "sequence_continuity": cross.sequence_continuity,
        "duplicates_dropped": cross.duplicates_dropped,
        "suspicious_pages": cross.suspicious_pages,
    }

    return out


# ════════════════════════════════════════════════════════════════════════════
# Stage 5.3 v3 — paper_type aware + 학습자료 strict pruning + Tier 1 명시
# ════════════════════════════════════════════════════════════════════════════
#
# v2 한계 (Stage 5.2 평가):
#  - 학습자료(객서심화/복습과제/내지) recall 2.5~7.5x 잔존
#  - 학습자료 페이지의 "예제 N", "Step N", 챕터 sub-section 번호도 line_start 통과
#  - 운영 paper_type 분류기를 prototype 안에 흡수하지 않아 자료 종류별 정책 분기 X
#
# v3 보강:
#  1. paper_type prototype 분류기 (파일명 + 본문 키워드 + anchor density + 선택지 패턴)
#  2. paper_type 별 anchor 정책:
#     - exam/mock/killer_test: v2 그대로
#     - review_homework/advanced_material/workbook_main: strict (선택지 동반 검증 필수)
#     - answer_explanation/cover: anchor 0
#     - unknown: low_confidence 분류
#  3. anchor 선택지 동반 검증 (학습자료 strict 모드)
#  4. y-gap < 임계값 → 본문 항목 의심
#  5. 문서 수준 over-detection 경고 (anchor > expected*2)
#  6. tier1_required 명시 (scanned PDF / partial text)
#
# 운영 OCR 호출은 미진행 (credentials 없음 + 비용 — 사용자 directive 준수).

PAPER_TYPE_EXAM = "exam"
PAPER_TYPE_MOCK_EXAM = "mock_exam"
PAPER_TYPE_KILLER_TEST = "killer_test"
PAPER_TYPE_REVIEW_HOMEWORK = "review_homework"
PAPER_TYPE_ADVANCED_MATERIAL = "advanced_material"
PAPER_TYPE_WORKBOOK_MAIN = "workbook_main"
PAPER_TYPE_ANSWER_EXPLANATION = "answer_explanation"
PAPER_TYPE_COVER = "cover"
PAPER_TYPE_UNKNOWN = "unknown"

# 학습자료 paper_type — strict anchor pruning 대상
_LEARNING_MATERIAL_PAPER_TYPES = frozenset({
    PAPER_TYPE_REVIEW_HOMEWORK,
    PAPER_TYPE_ADVANCED_MATERIAL,
    PAPER_TYPE_WORKBOOK_MAIN,
})

# 시험지 paper_type — v2 정책 그대로
_EXAM_PAPER_TYPES = frozenset({
    PAPER_TYPE_EXAM,
    PAPER_TYPE_MOCK_EXAM,
    PAPER_TYPE_KILLER_TEST,
})

# 파일명 키워드 → paper_type 추정
_FILENAME_HINTS = (
    # 학습자료류 (가장 강한 신호 — 운영 분포에서 over-detection 주범)
    ("복습과제", PAPER_TYPE_REVIEW_HOMEWORK),
    ("객서심화", PAPER_TYPE_ADVANCED_MATERIAL),
    ("객·서", PAPER_TYPE_ADVANCED_MATERIAL),
    ("객서", PAPER_TYPE_ADVANCED_MATERIAL),
    ("심화", PAPER_TYPE_ADVANCED_MATERIAL),
    ("내지", PAPER_TYPE_WORKBOOK_MAIN),
    ("메인자료", PAPER_TYPE_WORKBOOK_MAIN),
    ("개념완성", PAPER_TYPE_WORKBOOK_MAIN),
    ("문항편", PAPER_TYPE_WORKBOOK_MAIN),
    ("workbook", PAPER_TYPE_WORKBOOK_MAIN),
    # 시험지/모의고사/킬러
    ("모의고사", PAPER_TYPE_MOCK_EXAM),
    ("파이널", PAPER_TYPE_MOCK_EXAM),
    ("내신용", PAPER_TYPE_MOCK_EXAM),
    ("킬러", PAPER_TYPE_KILLER_TEST),
    ("killer", PAPER_TYPE_KILLER_TEST),
    ("기출", PAPER_TYPE_EXAM),
    ("중간고사", PAPER_TYPE_EXAM),
    ("기말고사", PAPER_TYPE_EXAM),
)

# 본문 키워드 → paper_type 보조 신호 (파일명 hint 없을 때)
_BODY_KEYWORD_HINTS = (
    ("복습 과제", PAPER_TYPE_REVIEW_HOMEWORK),
    ("탐구 활동", PAPER_TYPE_WORKBOOK_MAIN),
    ("Step ", PAPER_TYPE_WORKBOOK_MAIN),
    ("개념 정리", PAPER_TYPE_WORKBOOK_MAIN),
    ("정답과 해설", PAPER_TYPE_ANSWER_EXPLANATION),
    ("정답 및 해설", PAPER_TYPE_ANSWER_EXPLANATION),
)

# 선택지 패턴 — 학습자료 strict 모드의 anchor 동반 검증
_CHOICE_PATTERN_RE = re.compile(r"[①②③④⑤]|ㄱ\.|ㄴ\.|ㄷ\.|보기에서|다음 중|옳은\s*것|옳지\s*않은")

# 학습자료 strict 모드: anchor 주변 ±20개 word 안에 선택지/문제형 키워드 등장 필수
_LEARNING_STRICT_NEIGHBOR_RANGE = 20

# y-gap pruning — 같은 column 안 인접 anchor 사이 y-gap 최소값 (PDF points)
_MIN_ANCHOR_Y_GAP = 30.0

# 페이지당 anchor 임계값 (이 이상이면 suspicious)
_MAX_ANCHORS_PER_PAGE = 30


def classify_paper_type_prototype(
    *,
    file_name: str = "",
    pages_full_text: str = "",
    total_anchors: int = 0,
    page_count: int = 1,
) -> tuple[str, float, dict]:
    """파일명 + 본문 + anchor density 휴리스틱으로 paper_type 추정.

    운영 academy.domain.tools.paper_type 와는 분리된 prototype — 9-class 운영 enum 대신
    학습자료/시험지/answer/cover 등 dispatcher 정책 분기에 필요한 카테고리만.

    Returns:
        (paper_type, confidence, debug)
    """
    debug: dict = {"file_name": file_name}
    fn_lower = (file_name or "").lower()

    # 1. 파일명 hint (가장 강한 신호)
    for kw, pt in _FILENAME_HINTS:
        if kw.lower() in fn_lower:
            debug["filename_match"] = kw
            return (pt, 0.85, debug)

    # 2. 본문 키워드 hint
    for kw, pt in _BODY_KEYWORD_HINTS:
        if kw in pages_full_text:
            debug["body_keyword_match"] = kw
            return (pt, 0.7, debug)

    # 3. anchor density 휴리스틱
    if page_count > 0:
        anchors_per_page = total_anchors / page_count
        debug["anchors_per_page"] = round(anchors_per_page, 2)
        # 너무 많은 anchor — 학습자료 의심
        if anchors_per_page >= 25:
            return (PAPER_TYPE_ADVANCED_MATERIAL, 0.6, debug)
        # 정상 시험지 범위
        if 1 <= anchors_per_page <= 5:
            return (PAPER_TYPE_EXAM, 0.5, debug)

    return (PAPER_TYPE_UNKNOWN, 0.3, debug)


def _has_choice_pattern_nearby(
    anchor: NumberAnchor, word_blocks: list[dict],
) -> bool:
    """anchor 주변 ±20 word 안에 선택지/문제형 키워드 동반 여부.

    학습자료 strict 모드에서 본문 inline 항목번호 차단.
    """
    if not word_blocks:
        return False
    # anchor word index 찾기 (bbox 일치)
    ax, ay = anchor.bbox[0], anchor.bbox[1]
    sorted_w = sorted(word_blocks, key=lambda w: (w["y0"], w["x0"]))
    anchor_idx = -1
    for i, w in enumerate(sorted_w):
        if abs(w["x0"] - ax) < 1.0 and abs(w["y0"] - ay) < 1.0:
            anchor_idx = i
            break
    if anchor_idx < 0:
        return False
    start = max(0, anchor_idx - _LEARNING_STRICT_NEIGHBOR_RANGE)
    end = min(len(sorted_w), anchor_idx + _LEARNING_STRICT_NEIGHBOR_RANGE + 1)
    neighbor_text = " ".join(w.get("text", "") for w in sorted_w[start:end])
    return bool(_CHOICE_PATTERN_RE.search(neighbor_text))


def _filter_anchors_by_y_gap(
    anchors: list[NumberAnchor], min_gap: float = _MIN_ANCHOR_Y_GAP,
) -> list[NumberAnchor]:
    """같은 column (x0 근접) 안 인접 anchor 사이 y-gap 너무 작으면 본문 항목 의심 — 후순위 drop.

    sort: y0 ↑. 인접 anchor 의 y0 차이가 min_gap 미만이면 후순위 (number 가 더 큰 것) drop.
    """
    if len(anchors) <= 1:
        return anchors

    # column 기준 grouping (x0 ±50pt 그룹)
    sorted_a = sorted(anchors, key=lambda a: (a.bbox[0], a.bbox[1]))
    groups: list[list[NumberAnchor]] = []
    for a in sorted_a:
        placed = False
        for g in groups:
            if abs(g[0].bbox[0] - a.bbox[0]) < 50.0:
                g.append(a)
                placed = True
                break
        if not placed:
            groups.append([a])

    kept: list[NumberAnchor] = []
    for group in groups:
        group.sort(key=lambda a: a.bbox[1])
        prev_y = -float("inf")
        for a in group:
            if a.bbox[1] - prev_y < min_gap:
                # too close — 본문 항목 의심, drop
                continue
            kept.append(a)
            prev_y = a.bbox[1]
    kept.sort(key=lambda a: (a.bbox[1], a.bbox[0]))
    return kept


def detect_problem_anchors_v3(
    page: PageBlocks, columns: ColumnLayout, paper_type: str,
) -> list[NumberAnchor]:
    """v3 anchor 검출 — paper_type-aware + 학습자료 strict + y-gap pruning.

    paper_type 별 정책:
    - answer_explanation / cover: anchor 0 (page_role 단계에서 차단되지만 안전망)
    - 학습자료: 선택지 동반 검증 + y-gap pruning + 페이지 max anchor 제한
    - exam/mock/killer: v2 그대로 (이미 정확)
    - unknown: v2 + y-gap pruning (보수적)
    """
    if paper_type in (PAPER_TYPE_ANSWER_EXPLANATION, PAPER_TYPE_COVER):
        return []

    # v2 base (line_start strict + 60 상한 + column 근접 + dedup)
    base = detect_problem_anchors_v2(page, columns)
    if not base:
        return []

    # 학습자료 strict 모드: 선택지 동반 검증 필수
    if paper_type in _LEARNING_MATERIAL_PAPER_TYPES:
        with_choice = [a for a in base if _has_choice_pattern_nearby(a, page.word_blocks)]
        # 너무 엄격하면 0개 — 페이지에 선택지 패턴이 있으면 그대로 유지, 없으면 v2 그대로 (페이지 자체가 학습자료 본문이라 의심)
        if with_choice:
            base = with_choice
        else:
            # 선택지가 페이지 어디에도 없는 경우 — 학습자료 본문으로 추정해서 anchor 비움
            return []

    # y-gap pruning (학습자료 + unknown)
    if paper_type in _LEARNING_MATERIAL_PAPER_TYPES or paper_type == PAPER_TYPE_UNKNOWN:
        base = _filter_anchors_by_y_gap(base)

    # 페이지 max anchor (학습자료 + unknown 만 적용)
    if paper_type in _LEARNING_MATERIAL_PAPER_TYPES or paper_type == PAPER_TYPE_UNKNOWN:
        if len(base) > _MAX_ANCHORS_PER_PAGE:
            # 너무 많음 — 본문 항목 폭증 의심, suspicious
            base = []  # paper_type aware 모드: 학습자료에서 anchor 폭증은 본문일 가능성 ↑

    return base


def analyze_pdf_v3(pdf_path: str, *, file_name: Optional[str] = None) -> dict[str, Any]:
    """v3 통합 entrypoint — paper_type 결합 + 학습자료 strict + Tier 1 명시.

    file_name optional — 운영 호출자가 doc.original_name 전달 권장.
    None 이면 pdf_path 의 basename 사용.
    """
    import os as _os

    pages = extract_page_blocks(pdf_path)
    n_pages = len(pages)
    n_text_pages = sum(1 for p in pages if p.has_embedded_text)

    if file_name is None:
        file_name = _os.path.basename(pdf_path)

    # tier1_required (v2 와 동일)
    tier1_required = False
    tier1_reason = ""
    if n_pages == 0:
        tier1_required = True
        tier1_reason = "empty_pdf"
    elif n_text_pages == 0:
        tier1_required = True
        tier1_reason = "scanned_no_text_layer"
    elif n_text_pages < n_pages * 0.5:
        tier1_required = True
        tier1_reason = "partial_text_layer"

    # 1차 — v2 anchor 사용해서 paper_type 분류 신호 수집
    pre_anchors_total = 0
    full_text_chunks: list[str] = []
    per_page_columns: list[ColumnLayout] = []
    for page in pages:
        cols = detect_columns(page.word_blocks, page.page_width)
        per_page_columns.append(cols)
        v2_anchors = detect_problem_anchors_v2(page, cols)
        pre_anchors_total += len(v2_anchors)
        full_text_chunks.append(
            " ".join(b.get("text", "") for b in page.text_blocks)[:300]
        )
    full_text = " ".join(full_text_chunks)

    paper_type, pt_conf, pt_debug = classify_paper_type_prototype(
        file_name=file_name,
        pages_full_text=full_text,
        total_anchors=pre_anchors_total,
        page_count=n_pages,
    )

    # 2차 — paper_type-aware anchor 검출
    per_page_anchors: list[list[NumberAnchor]] = []
    for page, cols in zip(pages, per_page_columns):
        anchors = detect_problem_anchors_v3(page, cols, paper_type)
        per_page_anchors.append(anchors)

    cross = cross_page_validate(per_page_anchors)

    out: dict[str, Any] = {
        "version": "v3",
        "pdf_path": pdf_path,
        "file_name": file_name,
        "page_count": n_pages,
        "text_pages": n_text_pages,
        "paper_type": paper_type,
        "paper_type_confidence": pt_conf,
        "paper_type_debug": pt_debug,
        "tier1_required": tier1_required,
        "tier1_reason": tier1_reason,
        "pages": [],
        "cross_page": {
            "detected_total": cross.detected_total,
            "expected_max": cross.expected_max,
            "sequence_continuity": cross.sequence_continuity,
            "duplicates_dropped": cross.duplicates_dropped,
            "suspicious_pages": cross.suspicious_pages,
        },
    }

    for page, anchors, cols in zip(pages, per_page_anchors, per_page_columns):
        role = classify_page_role_v2(page, anchors)
        candidates = derive_bbox_candidates_v2(anchors, page, cols)
        out["pages"].append({
            "page_index": page.page_index,
            "page_width": page.page_width,
            "page_height": page.page_height,
            "has_embedded_text": page.has_embedded_text,
            "role": role.role,
            "role_confidence": role.confidence,
            "role_debug": role.debug,
            "anchor_count": len(anchors),
            "anchors": [asdict(a) for a in anchors],
            "bbox_candidates": [asdict(c) for c in candidates],
            "columns": {
                "count": cols.column_count,
                "lefts": cols.column_lefts,
            },
        })

    return out
