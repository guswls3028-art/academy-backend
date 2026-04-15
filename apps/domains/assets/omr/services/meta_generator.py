# apps/domains/assets/omr/services/meta_generator.py
"""
OMR v14 좌표 메타 생성기 — SSOT

이 파일은 pdf_renderer.py(렌더링 SSOT)와 동기화된 mm 단위 좌표를 정의한다.

v14 변경 (v13 대비):
  - 코너 마커 5mm 비대칭 채움 도형 + ㄱ자 브래킷 (마커 off=2.5, 브래킷 분리)
  - 인식 마크: 버블 좌표에 정렬된 1.5mm 사각형 (컬럼 x, 행 y 기준점)
  - 통합 프레임 (v8 깔끔 톤, 부드러운 회색 선)
  - 4코너 비대칭 기준 마크(markers) 유지 (TL=square, TR=L, BL=triangle, BR=plus)
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
    """v14 4코너 비대칭 기준 마크 좌표 — 5mm 채움 도형 + ㄱ자 브래킷.

    pdf_renderer._corners()와 동기화.
    마커 크기 5mm, 팔 두께 1.5mm, 오프셋 2.5mm.
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
    """OMR 메타 생성 (좌표 SSOT). v14: 5mm 비대칭 마커 + 버블 정렬 인식마크."""
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
        body_y=body_y, body_h=body_h, n_choices=n_choices,
    )

    return {
        "version": "v14",
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


# ── 인식 마크 상수 v14 (pdf_renderer._render_timing과 동기화) ──
# 한국식 바코드 스트립: 버블 x / 행 y에 1:1 대응하는 바 좌표
_TM_VBAR_W = 0.7       # 세로 바 폭 (상하단)
_TM_VBAR_H = 3.0       # 세로 바 높이
_TM_HBAR_W = 3.0       # 가로 바 폭 (좌우 일반)
_TM_HBAR_H = 0.7       # 가로 바 높이
_TM_HBAR_W5 = 4.0      # 5행 강조 폭
_TM_HBAR_H5 = 0.85     # 5행 강조 높이
_TM_HBAR_W10 = 4.5     # 10행 강조 폭
_TM_HBAR_H10 = 1.0     # 10행 강조 높이
_TM_GAP = 1.0          # 프레임 ↔ 마크 간격


def _build_timing_marks_meta(
    *, n_cols: int, per_col: int, question_count: int,
    body_y: float, body_h: float, n_choices: int = 5,
) -> Dict[str, Any]:
    """한국식 바코드 타이밍 마크 좌표 (pdf_renderer._render_timing과 동기화).

    상하단: 각 버블 x 중심 + 컬럼 경계에 세로 바
    좌우: 각 행 y 중심에 가로 바 (5행/10행 강조)
    """
    vw = _TM_VBAR_W
    vh = _TM_VBAR_H
    gap = _TM_GAP

    # ── 버블 x 좌표 + 컬럼 경계 수집 ──
    all_xs: List[float] = []
    for ci in range(n_cols):
        col_x = ANS_X + ci * (MC_COL_W + MC_COL_GAP)
        all_xs.append(col_x)
        all_xs.append(col_x + MC_COL_W)
        bxs = _calc_bubble_centers_x(col_x, n_choices)
        all_xs.extend(bxs)
    all_xs = sorted(set(round(x, 2) for x in all_xs))

    # ── 상단 바 좌표 ──
    top_y = CONTENT_Y - gap - vh
    top_bars = [
        {"x": round(bx - vw / 2, 2), "y": round(top_y, 2), "w": vw, "h": vh}
        for bx in all_xs
    ]

    # ── 하단 바 좌표 ──
    bot_y = CONTENT_Y + CONTENT_H + gap
    bottom_bars = [
        {"x": round(bx - vw / 2, 2), "y": round(bot_y, 2), "w": vw, "h": vh}
        for bx in all_xs
    ]

    # ── 좌측 바 좌표 (첫 컬럼 기준, 모든 행) ──
    left_bars: List[Dict[str, Any]] = []
    if n_cols > 0:
        cnt = min(per_col, question_count)
        if cnt > 0:
            rh = body_h / cnt
            for qi in range(cnt):
                row_cy = body_y + (qi + 0.5) * rh
                q_num = qi + 1
                if q_num % 10 == 0:
                    bw, bh_val = _TM_HBAR_W10, _TM_HBAR_H10
                elif q_num % 5 == 0:
                    bw, bh_val = _TM_HBAR_W5, _TM_HBAR_H5
                else:
                    bw, bh_val = _TM_HBAR_W, _TM_HBAR_H
                left_bars.append({
                    "row": qi, "x": 1.0,
                    "y": round(row_cy - bh_val / 2, 2),
                    "w": bw, "h": bh_val,
                })

    # ── 우측 바 좌표 (마지막 컬럼 기준) ──
    right_bars: List[Dict[str, Any]] = []
    if n_cols > 0:
        last_ci = n_cols - 1
        s = last_ci * per_col + 1
        e = min(s + per_col - 1, question_count)
        cnt = e - s + 1
        if cnt > 0:
            rh = body_h / cnt
            for qi in range(cnt):
                row_cy = body_y + (qi + 0.5) * rh
                q_num = qi + 1
                if q_num % 10 == 0:
                    bw, bh_val = _TM_HBAR_W10, _TM_HBAR_H10
                elif q_num % 5 == 0:
                    bw, bh_val = _TM_HBAR_W5, _TM_HBAR_H5
                else:
                    bw, bh_val = _TM_HBAR_W, _TM_HBAR_H
                right_bars.append({
                    "row": qi, "x": round(PAGE_W - 1.0 - bw, 2),
                    "y": round(row_cy - bh_val / 2, 2),
                    "w": bw, "h": bh_val,
                })

    return {
        "top_bars": top_bars,
        "bottom_bars": bottom_bars,
        "left_bars": left_bars,
        "right_bars": right_bars,
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
