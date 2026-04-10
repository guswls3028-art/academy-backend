# apps/domains/assets/omr/services/meta_generator.py
"""
OMR v13 좌표 메타 생성기 — SSOT

이 파일은 pdf_renderer.py(렌더링 SSOT)와 동기화된 mm 단위 좌표를 정의한다.

v13 변경:
  - 코너 마커 8mm로 확대 (인식률 향상)
  - 에지 인식 마크 추가 (상하좌우 촘촘 배치)
  - 페이지 외곽 테두리 3pt 추가
  - 섹션 외곽 테두리 2.5pt로 강화
  - 타이밍 마크 시스템 유지 (수능 스타일)
  - 4코너 비대칭 기준 마크(markers) 유지
  - identifier / MC column 로컬 앵커 유지

AI 워커는 이 메타를 사용하여 스캔된 이미지에서 버블 위치를 찾는다.
pdf_renderer.py 레이아웃이 변경되면 이 파일도 반드시 함께 수정해야 한다.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List

# ══════════════════════════════════════════
# 페이지 상수 (omr-sheet.html CSS와 동기화)
# ══════════════════════════════════════════
PAGE_W = 297.0
PAGE_H = 210.0

MARGIN_L = 10.0
MARGIN_T = 9.0
MARGIN_R = 10.0
MARGIN_B = 8.0

CONTENT_X = MARGIN_L
CONTENT_Y = MARGIN_T
CONTENT_W = PAGE_W - MARGIN_L - MARGIN_R   # 277mm
CONTENT_H = PAGE_H - MARGIN_T - MARGIN_B   # 195mm

# ── Left Panel ──
LP_W = 62.0
LP_GAP = 3.0
LP_BORDER = 0.5
LP_PAD_X = 2.5

# ── Answer Area ──
ANS_X = CONTENT_X + LP_W + LP_GAP          # 75mm

# ── MC Column ──
MC_COL_W = 44.0
MC_COL_GAP = 2.5
MC_HEADER_H = 5.5
MC_NUM_W = 8.0
MC_BUB_PAD = 0.5

# ── Bubble (쌀톨형 세로 타원) ──
BUB_W = 3.6
BUB_H = 5.2

# ── Identifier (전화번호 뒤 8자리) ──
ID_DIGIT_W = 5.8
ID_SEP_W = 3.5
ID_BUB_W = 3.2
ID_BUB_H = 4.2      # 축소 (5.2→4.2) — 간격 확보로 인식률 향상
ID_BUB_GAP = 1.2     # 확대 (0.6→1.2) — 인접 버블 혼동 방지
ID_DIGITS = 8
ID_VALUES = 10

# ══════════════════════════════════════════
# 레이아웃 안전 제한 (판독 안정성 최우선)
# ══════════════════════════════════════════
MIN_ROW_H_MM = 8.0              # 행 최소 높이 (버블 5.2mm + 여백)
MIN_VERTICAL_GAP_MM = 2.0       # 버블 간 최소 수직 간격
MAX_MC_QUESTIONS = 60           # 1페이지 최대 객관식 수
MAX_QUESTIONS_PER_COL = 20      # 컬럼당 최대 문항 수
MAX_COLS = 3                    # 최대 컬럼 수


def compute_safe_layout(question_count: int) -> Dict[str, Any]:
    """
    문항수에 대해 안전한 컬럼/행 레이아웃 결정.

    Returns:
        {"n_cols": int, "per_col": int, "row_h_mm": float, "safe": bool, "reason": str}
    """
    if question_count <= 0:
        return {"n_cols": 0, "per_col": 0, "row_h_mm": 0, "safe": True, "reason": ""}

    body_h = CONTENT_H - MC_HEADER_H  # ~189.5mm

    if question_count > MAX_MC_QUESTIONS:
        return {
            "n_cols": 0, "per_col": 0, "row_h_mm": 0, "safe": False,
            "reason": f"객관식 {question_count}문항은 1페이지 최대 {MAX_MC_QUESTIONS}문항을 초과합니다.",
        }

    # 최소 컬럼 수 결정: per_col <= MAX_QUESTIONS_PER_COL && row_h >= MIN_ROW_H_MM
    for n_cols in range(1, MAX_COLS + 1):
        per_col = math.ceil(question_count / n_cols)
        if per_col > MAX_QUESTIONS_PER_COL:
            continue
        row_h = body_h / per_col
        if row_h >= MIN_ROW_H_MM:
            return {
                "n_cols": n_cols, "per_col": per_col,
                "row_h_mm": round(row_h, 2), "safe": True, "reason": "",
            }

    # 3컬럼으로도 안전 기준 미달 → 차단
    per_col = math.ceil(question_count / MAX_COLS)
    row_h = body_h / per_col
    return {
        "n_cols": MAX_COLS, "per_col": per_col,
        "row_h_mm": round(row_h, 2), "safe": False,
        "reason": f"객관식 {question_count}문항: 행 높이 {round(row_h, 1)}mm < 최소 {MIN_ROW_H_MM}mm. 문항 수를 줄여주세요.",
    }


def validate_layout(question_count: int, essay_count: int = 0) -> List[str]:
    """레이아웃 유효성 검증. 에러 메시지 리스트 반환 (빈 리스트 = OK)."""
    errors: List[str] = []
    if question_count < 0:
        errors.append("객관식 문항 수는 0 이상이어야 합니다.")
    if essay_count < 0:
        errors.append("서술형 문항 수는 0 이상이어야 합니다.")
    if question_count > MAX_MC_QUESTIONS:
        errors.append(f"객관식 {question_count}문항은 1페이지 최대 {MAX_MC_QUESTIONS}문항을 초과합니다.")
    layout = compute_safe_layout(question_count)
    if not layout["safe"]:
        errors.append(layout["reason"])

    # 컬럼 수 × 폭이 페이지를 초과하는지
    n_cols = layout["n_cols"]
    total_mc_w = n_cols * MC_COL_W + max(0, n_cols - 1) * MC_COL_GAP
    ans_w = PAGE_W - MARGIN_R - ANS_X  # 사용 가능 너비
    if essay_count > 0:
        min_essay_w = 40.0
        if total_mc_w + MC_COL_GAP + min_essay_w > ans_w:
            errors.append(f"객관식 {n_cols}컬럼 + 서술형이 페이지 너비를 초과합니다. 문항 수를 줄여주세요.")

    return errors


def _calc_bubble_centers_x(col_x: float, n_choices: int) -> List[float]:
    """MC column 내 버블 중심 x좌표 (space-evenly)."""
    area_x = col_x + MC_NUM_W + MC_BUB_PAD
    area_w = MC_COL_W - MC_NUM_W - 2 * MC_BUB_PAD
    n_gaps = n_choices + 1
    gap = (area_w - n_choices * BUB_W) / n_gaps
    return [
        round(area_x + gap * (i + 1) + BUB_W * i + BUB_W / 2, 2)
        for i in range(n_choices)
    ]


def _build_marker_meta() -> Dict[str, Any]:
    """v14 4코너 비대칭 기준 마크 좌표 — 소형 채움 도형 + ㄱ자 브래킷.

    pdf_renderer._corners()와 동기화.
    마커 크기 4mm (v13 8mm에서 축소), 팔 두께 1.2mm.
    """
    off = 2.5   # 페이지 가장자리 오프셋
    sz = 5.0    # 마커 크기
    th = 1.5    # 팔 두께
    pw, ph = PAGE_W, PAGE_H

    return {
        "TL": {
            "type": "square",
            "center": {"x": off + sz / 2, "y": off + sz / 2},
            "size": sz,
        },
        "TR": {
            "type": "l_shape",
            "center": {"x": pw - off - sz / 2, "y": off + sz / 2},
            "size": sz,
            "thickness": th,
        },
        "BL": {
            "type": "triangle",
            "center": {"x": off + sz / 2, "y": ph - off - sz / 2},
            "size": sz,
        },
        "BR": {
            "type": "plus",
            "center": {"x": pw - off - sz / 2, "y": ph - off - sz / 2},
            "size": sz,
            "thickness": th,
        },
    }


def build_omr_meta(
    *,
    question_count: int,
    n_choices: int = 5,
    essay_count: int = 0,
) -> Dict[str, Any]:
    """OMR 메타 생성 (좌표 SSOT). v13: 8mm 코너마커 + 에지마크 + 굵은 외곽."""
    layout = compute_safe_layout(question_count)
    per_col = layout["per_col"]
    n_cols = layout["n_cols"]

    body_y = CONTENT_Y + MC_HEADER_H
    body_h = CONTENT_H - MC_HEADER_H

    questions: List[Dict[str, Any]] = []
    columns: List[Dict[str, Any]] = []

    for c in range(n_cols):
        col_x = ANS_X + c * (MC_COL_W + MC_COL_GAP)
        start = c * per_col + 1
        end = min(start + per_col - 1, question_count)
        count_in_col = end - start + 1
        row_h = body_h / count_in_col if count_in_col > 0 else body_h

        bubble_xs = _calc_bubble_centers_x(col_x, n_choices)

        col_questions: List[Dict[str, Any]] = []
        for q_idx in range(count_in_col):
            q_num = start + q_idx
            row_cy = body_y + (q_idx + 0.5) * row_h
            choices = [
                {
                    "label": str(ci + 1),
                    "center": {"x": bx, "y": round(row_cy, 2)},
                    "radius_x": round(BUB_W / 2, 2),
                    "radius_y": round(BUB_H / 2, 2),
                }
                for ci, bx in enumerate(bubble_xs)
            ]
            q_entry = {
                "question_number": q_num,
                "type": "choice",
                "column": c,  # column index for per-column alignment
                "roi": {
                    "x": round(col_x, 2),
                    "y": round(row_cy - row_h / 2, 2),
                    "w": round(MC_COL_W, 2),
                    "h": round(row_h, 2),
                },
                "choices": choices,
            }
            questions.append(q_entry)
            col_questions.append(q_entry)

        # Column anchors: top and bottom alignment marks
        col_anchors = {
            "top": {
                "type": "circle",
                "center": {
                    "x": round(col_x + MC_COL_W - 2.0, 2),
                    "y": round(body_y - 1.5, 2),
                },
                "radius": 1.5,
            },
            "bottom": {
                "type": "circle",
                "center": {
                    "x": round(col_x + 2.0, 2),
                    "y": round(body_y + body_h + 1.0, 2),
                },
                "radius": 1.5,
            },
        }
        columns.append({
            "column_index": c,
            "col_x": round(col_x, 2),
            "questions": col_questions,
            "anchors": col_anchors,
        })

    # 타이밍 마크 좌표 (pdf_renderer와 동기화)
    timing_marks = _build_timing_marks_meta(
        n_cols=n_cols, per_col=per_col, question_count=question_count,
        body_y=body_y, body_h=body_h,
    )

    return {
        "version": "v13",
        "units": "mm",
        "page": {"width": PAGE_W, "height": PAGE_H},
        "markers": _build_marker_meta(),
        "mc_count": question_count,
        "essay_count": essay_count,
        "n_choices": n_choices,
        "layout": {
            "n_cols": n_cols,
            "per_col": per_col,
            "row_h_mm": layout["row_h_mm"],
            "safe": layout["safe"],
        },
        "questions": questions,           # flat list (backward compatible)
        "columns": columns,               # grouped by column with anchors
        "timing_marks": timing_marks,     # 행 정렬용 타이밍 마크
        "identifier": _build_identifier_meta(),
    }


# ── 타이밍 마크 상수 v12 (pdf_renderer와 동기화 — 수능 스타일) ──

# 상하단 바코드 바
TM_BAR_H = 2.5        # 바 높이 (mm)
TM_BAR_W = 6.0        # 개별 바 폭 (mm)
TM_BAR_GAP = 2.0      # 바 사이 간격 (mm)
TM_TOP_Y = -5.0       # CONTENT_Y 위로 5mm
TM_BOT_Y = 2.5        # CONTENT_Y + CONTENT_H 아래로 2.5mm

# 좌우 행 바 (5행 간격만 — 외곽 컬럼만, 테두리에서 gap만큼 떨어짐)
TM_G5_LEN = 2.5       # 5행 돌출 길이
TM_G10_LEN = 3.5      # 10행 돌출 길이
TM_H_G5 = 0.8         # 5행 바 높이
TM_H_G10 = 1.2        # 10행 바 높이
TM_GAP = 0.5          # 컬럼 테두리에서 간격


def _build_timing_marks_meta(
    *, n_cols: int, per_col: int, question_count: int,
    body_y: float, body_h: float,
) -> Dict[str, Any]:
    """v12 타이밍 마크 좌표 생성 (pdf_renderer._timing_marks와 동기화).

    수능 스타일:
    - 상하단: 답안 영역 전체 폭에 균일 간격 바코드 바
    - 좌우: 모든 컬럼에 행 바 (5행/10행 강조)
    - 갭 내 중간 컬럼은 돌출 길이 축소
    """
    # ── 상하단 바코드 바 ──
    ans_start = ANS_X
    ans_end = ANS_X + n_cols * MC_COL_W + max(0, n_cols - 1) * MC_COL_GAP
    total_w = ans_end - ans_start
    n_bars = max(1, int(total_w / (TM_BAR_W + TM_BAR_GAP)))
    actual_gap = (total_w - n_bars * TM_BAR_W) / max(1, n_bars - 1) if n_bars > 1 else 0

    top_bottom_bars: List[Dict[str, Any]] = []
    for pos_label, bar_y in [("top", CONTENT_Y + TM_TOP_Y), ("bottom", CONTENT_Y + CONTENT_H + TM_BOT_Y)]:
        for bi in range(n_bars):
            bx = ans_start + bi * (TM_BAR_W + actual_gap)
            top_bottom_bars.append({
                "position": pos_label,
                "index": bi,
                "x": round(bx, 2),
                "y": round(bar_y, 2),
                "w": TM_BAR_W,
                "h": TM_BAR_H,
            })

    # ── 좌우 행 바 (5행 간격만, 외곽 컬럼만) ──
    row_bars: List[Dict[str, Any]] = []
    for ci in range(n_cols):
        col_x = ANS_X + ci * (MC_COL_W + MC_COL_GAP)
        s = ci * per_col + 1
        e = min(s + per_col - 1, question_count)
        cnt = e - s + 1
        if cnt <= 0:
            continue
        rh = body_h / cnt

        is_first = (ci == 0)
        is_last = (ci == n_cols - 1)

        for qi in range(cnt):
            g10 = (qi % 10 == 0) and qi > 0
            g5 = (qi % 5 == 0)
            if not g5:
                continue

            rc = body_y + (qi + 0.5) * rh
            tm_len = TM_G10_LEN if g10 else TM_G5_LEN
            tm_h = TM_H_G10 if g10 else TM_H_G5

            # 좌측: 첫 컬럼만
            if is_first:
                row_bars.append({
                    "column": ci, "row": qi, "side": "left",
                    "emphasis": "g10" if g10 else "g5",
                    "x": round(col_x - TM_GAP - tm_len, 2),
                    "y": round(rc - tm_h / 2, 2),
                    "w": round(tm_len, 2),
                    "h": tm_h,
                })

            # 우측: 마지막 컬럼만
            if is_last:
                row_bars.append({
                    "column": ci, "row": qi, "side": "right",
                    "emphasis": "g10" if g10 else "g5",
                    "x": round(col_x + MC_COL_W + TM_GAP, 2),
                    "y": round(rc - tm_h / 2, 2),
                    "w": round(tm_len, 2),
                    "h": tm_h,
                })

    return {
        "top_bottom_bars": top_bottom_bars,
        "row_bars": row_bars,
    }


def _build_identifier_meta() -> Dict[str, Any]:
    """전화번호 뒤 8자리 버블 그리드 좌표."""
    lp_inner_x = CONTENT_X + LP_BORDER + LP_PAD_X
    grid_w = ID_DIGITS * ID_DIGIT_W + ID_SEP_W
    sec_pad = 2.5
    lp_content_w = LP_W - 2 * LP_BORDER - 2 * LP_PAD_X
    available_w = lp_content_w - 2 * sec_pad
    grid_offset_x = (available_w - grid_w) / 2
    grid_start_x = lp_inner_x + sec_pad + grid_offset_x

    # Y: 하단에서 역산 (lp-note ~25mm, phone section ~71mm)
    note_h = 25.0
    phone_sec_h = 71.0
    note_top = CONTENT_Y + CONTENT_H - note_h
    phone_sec_top = note_top - phone_sec_h
    bubbles_start_y = phone_sec_top + 14.5

    digits = []
    for d in range(ID_DIGITS):
        if d < 4:
            dx = grid_start_x + d * ID_DIGIT_W
        else:
            dx = grid_start_x + 4 * ID_DIGIT_W + ID_SEP_W + (d - 4) * ID_DIGIT_W
        cx = dx + ID_DIGIT_W / 2

        bubbles = [
            {
                "value": str(v),
                "center": {
                    "x": round(cx, 2),
                    "y": round(bubbles_start_y + v * (ID_BUB_H + ID_BUB_GAP) + ID_BUB_H / 2, 2),
                },
                "radius_x": round(ID_BUB_W / 2, 2),
                "radius_y": round(ID_BUB_H / 2, 2),
            }
            for v in range(ID_VALUES)
        ]
        digits.append({"digit_index": d, "bubbles": bubbles})

    # Identifier grid anchors for local alignment
    grid_end_x = grid_start_x + grid_w
    grid_end_y = bubbles_start_y + ID_VALUES * (ID_BUB_H + ID_BUB_GAP)
    anchors = {
        "TL": {
            "type": "square",
            "center": {
                "x": round(grid_start_x - 3.0, 2),
                "y": round(bubbles_start_y - 3.0, 2),
            },
            "size": 2.0,
        },
        "BR": {
            "type": "square",
            "center": {
                "x": round(grid_end_x + 1.0, 2),
                "y": round(grid_end_y, 2),
            },
            "size": 2.0,
        },
    }

    return {
        "version": "v9",
        "digits": digits,
        "digit_count": ID_DIGITS,
        "anchors": anchors,
    }


# ── 하위호환 래퍼 ──
def build_objective_template_meta(question_count: int, **kwargs) -> Dict[str, Any]:
    return build_omr_meta(question_count=question_count, **kwargs)
