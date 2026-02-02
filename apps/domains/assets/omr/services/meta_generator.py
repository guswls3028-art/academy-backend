from __future__ import annotations

from typing import Dict, List

from reportlab.lib.units import mm

from apps.domains.assets.omr import constants as C


def _pt_to_mm(v_pt: float) -> float:
    return float(v_pt) / float(mm)


def _mm_point(x_pt: float, y_pt: float) -> Dict[str, float]:
    return {"x": _pt_to_mm(x_pt), "y": _pt_to_mm(y_pt)}


def _mm_box(x_pt: float, y_pt: float, w_pt: float, h_pt: float) -> Dict[str, float]:
    return {"x": _pt_to_mm(x_pt), "y": _pt_to_mm(y_pt), "w": _pt_to_mm(w_pt), "h": _pt_to_mm(h_pt)}


def _choices_labels() -> List[str]:
    return ["A", "B", "C", "D", "E"]


def _question_bubbles_start_x(col_x: float) -> float:
    right_edge = col_x + C.COL_WIDTH - C.Q_RIGHT_PAD
    total_choice_width = (C.Q_CHOICE_COUNT - 1) * C.Q_CHOICE_GAP
    return right_edge - total_choice_width


def _question_area_y_bounds() -> tuple[float, float]:
    top = C.PAGE_HEIGHT - C.MARGIN_Y - C.Q_TOP_PAD
    bottom = C.MARGIN_Y + C.Q_BOTTOM_PAD
    return top, bottom


def build_objective_template_meta(*, question_count: int) -> Dict:
    """
    Objective meta for the NEW layout:
    - units: mm
    - 4 columns: left identifier + right 3 objective columns
    - objective questions are laid out as:
      col2: 1~15, col3: 16~30, col4: 31~45
    - IMPORTANT: only includes questions up to question_count; others are omitted.
    """
    if question_count not in C.ALLOWED_QUESTION_COUNTS:
        raise ValueError("invalid question_count")

    # -------------------------
    # Identifier bubbles (computed same philosophy: labels left, marking right)
    # -------------------------
    # We mirror layout math:
    # ident area is dynamic; for meta we compute using same formula as layout
    # so worker can rely on it for grading.
    top = C.PAGE_HEIGHT - C.MARGIN_Y
    logo_y = top - C.LOGO_BOX_H
    exam_y = logo_y - C.LEFT_BLOCK_GAP_1 - C.EXAMINFO_BOX_H
    name_y = exam_y - C.LEFT_BLOCK_GAP_2 - C.NAME_BOX_H

    ident_top = name_y - C.LEFT_BLOCK_GAP_3
    ident_bottom = C.MARGIN_Y
    ident_h = ident_top - ident_bottom

    title_reserved = 12 * mm
    usable_h = max(1.0, ident_h - title_reserved - 6 * mm)
    row_gap = usable_h / (C.IDENT_ROWS - 1)

    bubble_right = C.COL1_X + C.COL_WIDTH - C.IDENT_DIGIT_RIGHT_PAD
    col_gap = (C.COL_WIDTH - 22 * mm) / max(1, (C.IDENT_DIGITS - 1))
    total_digits_w = (C.IDENT_DIGITS - 1) * col_gap
    digits_left = bubble_right - total_digits_w - C.IDENT_EXTRA_RIGHT_GAP

    ident_bubbles: List[Dict] = []
    for n in range(10):
        y_pt = ident_bottom + 6 * mm + (9 - n) * row_gap
        for d in range(C.IDENT_DIGITS):
            x_pt = digits_left + d * col_gap
            ident_bubbles.append(
                {
                    "digit_index": int(d + 1),  # 1..8
                    "number": int(n),           # 0..9
                    "center": _mm_point(x_pt, y_pt),
                    "r": _pt_to_mm(C.IDENT_BUBBLE_R),
                }
            )

    identifier_meta = {
        "digits": int(C.IDENT_DIGITS),
        "numbers": list(range(10)),
        "bubbles": ident_bubbles,
    }

    # -------------------------
    # Questions bubbles + ROI
    # -------------------------
    top_y, bottom_y = _question_area_y_bounds()
    row_gap_q = (top_y - bottom_y) / (C.Q_ROWS_PER_COL - 1)

    roi_pad_pt = 2 * mm
    labels = _choices_labels()

    def build_col(col_x: float, start_q: int, rows: int) -> List[Dict]:
        bx0 = _question_bubbles_start_x(col_x)
        y_pt = top_y
        out: List[Dict] = []

        for i in range(rows):
            qnum = start_q + i
            if qnum > question_count:
                y_pt -= row_gap_q
                continue

            xs = []
            ys = []
            choice_bubbles = []
            for k, label in enumerate(labels):
                cx = bx0 + k * C.Q_CHOICE_GAP
                cy = y_pt
                xs.append(cx)
                ys.append(cy)
                choice_bubbles.append(
                    {
                        "label": label,
                        "center": _mm_point(cx, cy),
                        "r": _pt_to_mm(C.Q_BUBBLE_R),
                    }
                )

            left = min(xs) - C.Q_BUBBLE_R - roi_pad_pt
            right = max(xs) + C.Q_BUBBLE_R + roi_pad_pt
            bottom = min(ys) - C.Q_BUBBLE_R - roi_pad_pt
            topb = max(ys) + C.Q_BUBBLE_R + roi_pad_pt

            out.append(
                {
                    "question_number": int(qnum),
                    "axis": "x",
                    "choices": choice_bubbles,
                    "roi": _mm_box(left, bottom, right - left, topb - bottom),
                }
            )

            y_pt -= row_gap_q

        return out

    questions: List[Dict] = []
    questions += build_col(C.COL2_X, 1, C.Q_ROWS_PER_COL)
    questions += build_col(C.COL3_X, 16, C.Q_ROWS_PER_COL)
    questions += build_col(C.COL4_X, 31, C.Q_ROWS_PER_COL)

    meta = {
        "version": "objective_v2_45",
        "units": "mm",
        "question_count": int(question_count),
        "page": {
            "orientation": "landscape",
            "size": {"width": _pt_to_mm(C.PAGE_WIDTH), "height": _pt_to_mm(C.PAGE_HEIGHT)},
        },
        "identifier": identifier_meta,
        "questions": questions,
    }
    return meta
