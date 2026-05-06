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
