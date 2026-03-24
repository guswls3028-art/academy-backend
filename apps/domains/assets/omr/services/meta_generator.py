# apps/domains/assets/omr/services/meta_generator.py
"""
OMR v9 좌표 메타 생성기 — SSOT

이 파일은 omr-sheet.html(프론트엔드 SSOT)의 CSS 레이아웃과 동기화된
mm 단위 좌표를 정의한다.

v9 변경:
  - 하드코딩 mm 오프셋 제거 (grid_start_x += 6.0, -8.3 보정)
  - 4코너 비대칭 기준 마크(markers) 추가
  - identifier / MC column 로컬 앵커 추가
  - columns 필드 추가 (column-grouped questions + anchors)
  - flat questions 리스트는 하위호환을 위해 유지

AI 워커는 이 메타를 사용하여 스캔된 이미지에서 버블 위치를 찾는다.
omr-sheet.html 레이아웃이 변경되면 이 파일도 반드시 함께 수정해야 한다.
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
ID_BUB_W = 3.6
ID_BUB_H = 5.2
ID_BUB_GAP = 0.6
ID_DIGITS = 8
ID_VALUES = 10


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
    """4코너 비대칭 기준 마크 좌표."""
    return {
        "TL": {
            "type": "square",
            "center": {"x": 5.0, "y": 5.0},
            "size": 4.0,  # mm
        },
        "TR": {
            "type": "l_shape",
            "center": {"x": 292.0, "y": 5.0},
            "arm_length": 5.0,
            "stroke": 0.5,
        },
        "BL": {
            "type": "t_shape",
            "center": {"x": 5.0, "y": 205.0},
            "arm_h": 5.0,
            "arm_v": 3.0,
            "stroke": 0.5,
        },
        "BR": {
            "type": "plus",
            "center": {"x": 292.0, "y": 205.0},
            "arm_length": 5.0,
            "stroke": 0.5,
        },
    }


def build_omr_meta(
    *,
    question_count: int,
    n_choices: int = 5,
    essay_count: int = 0,
) -> Dict[str, Any]:
    """OMR 메타 생성 (좌표 SSOT)."""
    if question_count <= 0:
        per_col, n_cols = 0, 0
    elif question_count <= 20:
        per_col, n_cols = question_count, 1
    elif question_count <= 40:
        per_col = math.ceil(question_count / 2)
        n_cols = 2
    else:
        per_col = math.ceil(question_count / 3)
        n_cols = 3

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

    return {
        "version": "v9",
        "units": "mm",
        "page": {"width": PAGE_W, "height": PAGE_H},
        "markers": _build_marker_meta(),
        "mc_count": question_count,
        "essay_count": essay_count,
        "n_choices": n_choices,
        "questions": questions,           # flat list (backward compatible)
        "columns": columns,               # grouped by column with anchors
        "identifier": _build_identifier_meta(),
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
