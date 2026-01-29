# apps/domains/assets/omr/services/meta_generator.py
from __future__ import annotations

from typing import Dict, List, Tuple

from reportlab.lib.units import mm

from apps.domains.assets.omr import constants as C

ChoiceLabel = str


def _pt_to_mm(v_pt: float) -> float:
    # reportlab: mm = 2.8346..pt
    return float(v_pt) / float(mm)


def _mm_box(x_pt: float, y_pt: float, w_pt: float, h_pt: float) -> Dict[str, float]:
    return {
        "x": _pt_to_mm(x_pt),
        "y": _pt_to_mm(y_pt),
        "w": _pt_to_mm(w_pt),
        "h": _pt_to_mm(h_pt),
    }


def _mm_point(x_pt: float, y_pt: float) -> Dict[str, float]:
    return {"x": _pt_to_mm(x_pt), "y": _pt_to_mm(y_pt)}


def _identifier_digits_left_x() -> float:
    """
    PDF 레이아웃(objective_v1_10/20/30)에서 사용한 식별자 bubble x 정렬 계산을
    1:1로 그대로 복제한다. (단일진실: constants + 같은 공식)

    layouts:
      bubble_right_edge = COL1_X + COL_WIDTH - Q_RIGHT_PAD
      total_digits_width = (IDENT_DIGITS - 1) * IDENT_COL_GAP
      digits_left_x = bubble_right_edge - total_digits_width - 10*mm
    """
    bubble_right_edge = C.COL1_X + C.COL_WIDTH - C.Q_RIGHT_PAD
    total_digits_width = (C.IDENT_DIGITS - 1) * C.IDENT_COL_GAP
    return bubble_right_edge - total_digits_width - (10 * mm)


def _question_bubbles_start_x(col_x: float) -> float:
    """
    layouts의 bubbles_start_x를 1:1 복제.
      right_edge = col_x + COL_WIDTH - Q_RIGHT_PAD
      total_choice_width = (Q_CHOICE_COUNT - 1) * Q_CHOICE_GAP
      return right_edge - total_choice_width
    """
    right_edge = col_x + C.COL_WIDTH - C.Q_RIGHT_PAD
    total_choice_width = (C.Q_CHOICE_COUNT - 1) * C.Q_CHOICE_GAP
    return right_edge - total_choice_width


def _choices_labels() -> List[ChoiceLabel]:
    # 5지선다 고정
    return ["A", "B", "C", "D", "E"]


def build_objective_template_meta(*, question_count: int) -> Dict:
    """
    OMR objective template meta (stateless)
    - units: mm
    - PDF와 1:1로 대응되는 구조 정보만 제공 (채점/제출/DB 없음)
    """
    if question_count not in C.ALLOWED_QUESTION_COUNTS:
        raise ValueError("invalid question_count")

    page_w_pt, page_h_pt = C.PAGE_WIDTH, C.PAGE_HEIGHT

    # -------------------------
    # Identifier bubbles
    # -------------------------
    ident_bubbles: List[Dict] = []
    digits_left_x = _identifier_digits_left_x()

    for n in range(10):
        y_pt = (C.IDENT_AREA_BOTTOM + (9 - n) * C.IDENT_ROW_GAP)
        for d in range(C.IDENT_DIGITS):
            x_pt = digits_left_x + d * C.IDENT_COL_GAP
            ident_bubbles.append(
                {
                    "digit_index": int(d + 1),  # 1~8
                    "number": int(n),           # 0~9
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
    left_count, right_count = C.DISTRIBUTION_BY_COUNT[question_count]
    row_gap = C.ROW_GAP_BY_COUNT[question_count]

    roi_pad_pt = 2 * mm  # ROI bbox 여유(스캔/촬영 노이즈 대비). meta 전용, PDF 영향 없음.

    def build_col_questions(col_x: float, start_q: int, count: int) -> List[Dict]:
        bx0 = _question_bubbles_start_x(col_x)
        y_pt = C.Q_AREA_TOP
        out: List[Dict] = []

        labels = _choices_labels()
        for qi in range(count):
            qnum = start_q + qi

            choice_bubbles = []
            xs = []
            ys = []

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

            # ROI: 5개 버블을 감싸는 bbox (축정렬)
            left = min(xs) - C.Q_BUBBLE_R - roi_pad_pt
            right = max(xs) + C.Q_BUBBLE_R + roi_pad_pt
            bottom = (y_pt - C.Q_BUBBLE_R - roi_pad_pt)
            top = (y_pt + C.Q_BUBBLE_R + roi_pad_pt)

            roi = _mm_box(left, bottom, right - left, top - bottom)

            out.append(
                {
                    "question_number": int(qnum),
                    "choices": choice_bubbles,
                    "roi": roi,
                    "axis": "x",  # choices가 가로배치라는 단일 진실
                }
            )
            y_pt -= row_gap

        return out

    questions: List[Dict] = []
    questions += build_col_questions(C.COL2_X, start_q=1, count=left_count)
    questions += build_col_questions(C.COL3_X, start_q=left_count + 1, count=right_count)

    meta = {
        "version": "objective_v1",
        "units": "mm",
        "question_count": int(question_count),
        "page": {
            "orientation": "landscape",
            "size": {
                "width": _pt_to_mm(page_w_pt),
                "height": _pt_to_mm(page_h_pt),
            },
        },
        "identifier": identifier_meta,
        "questions": questions,
    }
    return meta
