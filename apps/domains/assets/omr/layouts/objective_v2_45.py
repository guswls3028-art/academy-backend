from __future__ import annotations

from reportlab.lib.units import mm

from apps.domains.assets.omr import constants as C


def draw(
    c,
    *,
    question_count: int,
    logo_reader=None,
    exam_title: str = "3월 모의고사",
    subject_round: str = "수학 (1회)",
) -> None:
    """
    Front page (Objective OMR)
    - 4 columns: left(logo/exam/name/identifier) + right 3 columns (1~15 / 16~30 / 31~45)
    - 5-question separators
    - print bubbles ONLY up to question_count; rest is blank
    """
    _draw_outer_frame(c)
    _draw_vertical_dividers(c)

    _draw_left_column(
        c,
        logo_reader=logo_reader,
        exam_title=exam_title,
        subject_round=subject_round,
    )

    _draw_objective_columns(c, question_count=question_count)


# =========================
# FRAME
# =========================
def _draw_outer_frame(c) -> None:
    c.setLineWidth(0.8)
    c.rect(
        C.MARGIN_X,
        C.MARGIN_Y,
        C.PAGE_WIDTH - 2 * C.MARGIN_X,
        C.PAGE_HEIGHT - 2 * C.MARGIN_Y,
    )


def _draw_vertical_dividers(c) -> None:
    # boundaries are at the midpoint of the gaps between columns
    c.setLineWidth(0.8)
    xs = [
        (C.COL2_X - (C.COL_GAP / 2)),
        (C.COL3_X - (C.COL_GAP / 2)),
        (C.COL4_X - (C.COL_GAP / 2)),
    ]
    for x in xs:
        c.line(x, C.MARGIN_Y, x, C.PAGE_HEIGHT - C.MARGIN_Y)


# =========================
# LEFT COLUMN (independent)
# =========================
def _draw_left_column(c, *, logo_reader, exam_title: str, subject_round: str) -> None:
    top = C.PAGE_HEIGHT - C.MARGIN_Y

    # --- Logo box
    logo_y = top - C.LOGO_BOX_H
    c.setLineWidth(0.8)
    c.rect(C.COL1_X, logo_y, C.COL_WIDTH, C.LOGO_BOX_H)

    c.setFont("Helvetica-Bold", 9)
    c.drawString(C.COL1_X + C.LEFT_PAD, logo_y + C.LOGO_BOX_H - 10, "로고")

    if logo_reader is not None:
        # preserve aspect ratio, fit inside box with padding
        c.drawImage(
            logo_reader,
            C.COL1_X + C.LEFT_PAD,
            logo_y + C.LEFT_PAD,
            width=C.COL_WIDTH - 2 * C.LEFT_PAD,
            height=C.LOGO_BOX_H - 2 * C.LEFT_PAD,
            preserveAspectRatio=True,
            anchor="c",
            mask="auto",
        )

    # --- Exam info box
    exam_y = logo_y - C.LEFT_BLOCK_GAP_1 - C.EXAMINFO_BOX_H
    c.rect(C.COL1_X, exam_y, C.COL_WIDTH, C.EXAMINFO_BOX_H)

    c.setFont("Helvetica-Bold", 9)
    c.drawString(C.COL1_X + C.LEFT_PAD, exam_y + C.EXAMINFO_BOX_H - 10, "시험명 / 과목")

    c.setFont("Helvetica", 10)
    c.drawString(C.COL1_X + C.LEFT_PAD, exam_y + C.EXAMINFO_BOX_H - 24, str(exam_title))
    c.drawString(C.COL1_X + C.LEFT_PAD, exam_y + C.EXAMINFO_BOX_H - 38, str(subject_round))

    # --- Name box
    name_y = exam_y - C.LEFT_BLOCK_GAP_2 - C.NAME_BOX_H
    c.rect(C.COL1_X, name_y, C.COL_WIDTH, C.NAME_BOX_H)

    c.setFont("Helvetica-Bold", 9)
    c.drawString(C.COL1_X + C.LEFT_PAD, name_y + C.NAME_BOX_H - 10, "이름")

    # writing line (right side is the “input area”)
    c.setLineWidth(0.6)
    c.line(
        C.COL1_X + C.LEFT_PAD,
        name_y + 6 * mm,
        C.COL1_X + C.COL_WIDTH - C.LEFT_PAD,
        name_y + 6 * mm,
    )
    c.setLineWidth(0.8)

    # --- Identifier area fills to bottom margin
    ident_top = name_y - C.LEFT_BLOCK_GAP_3
    ident_bottom = C.MARGIN_Y
    ident_h = ident_top - ident_bottom

    c.rect(C.COL1_X, ident_bottom, C.COL_WIDTH, ident_h)

    c.setFont("Helvetica-Bold", C.IDENT_TITLE_FONT_SIZE)
    c.drawString(
        C.COL1_X + C.LEFT_PAD,
        ident_top - 10,
        "수험번호 OMR (휴대폰 8자리, 010 제외)",
    )

    # compute row gap to fill area nicely
    # reserve a small title area at the top of identifier box
    title_reserved = 12 * mm
    usable_h = max(1.0, ident_h - title_reserved - 6 * mm)
    row_gap = usable_h / (C.IDENT_ROWS - 1)

    # right-aligned digit columns (marking area)
    bubble_right = C.COL1_X + C.COL_WIDTH - C.IDENT_DIGIT_RIGHT_PAD
    col_gap = (C.COL_WIDTH - 22 * mm) / max(1, (C.IDENT_DIGITS - 1))
    total_digits_w = (C.IDENT_DIGITS - 1) * col_gap
    digits_left = bubble_right - total_digits_w - C.IDENT_EXTRA_RIGHT_GAP

    c.setFont("Helvetica", C.IDENT_NUM_FONT_SIZE)
    for n in range(10):
        y = ident_bottom + 6 * mm + (9 - n) * row_gap
        # labels (left aligned)
        c.drawString(C.COL1_X + C.LEFT_PAD, y - 2 * mm, str(n))
        # bubbles (right aligned)
        for d in range(C.IDENT_DIGITS):
            x = digits_left + d * col_gap
            c.circle(x, y, C.IDENT_BUBBLE_R)


# =========================
# RIGHT 3 COLUMNS (objective)
# =========================
def _question_bubbles_start_x(col_x: float) -> float:
    right_edge = col_x + C.COL_WIDTH - C.Q_RIGHT_PAD
    total_choice_width = (C.Q_CHOICE_COUNT - 1) * C.Q_CHOICE_GAP
    return right_edge - total_choice_width


def _question_area_y_bounds() -> tuple[float, float]:
    top = C.PAGE_HEIGHT - C.MARGIN_Y - C.Q_TOP_PAD
    bottom = C.MARGIN_Y + C.Q_BOTTOM_PAD
    return top, bottom


def _draw_objective_columns(c, *, question_count: int) -> None:
    c.setFont("Helvetica", C.Q_FONT_SIZE)

    _draw_objective_one_col(c, col_x=C.COL2_X, start_q=1, question_count=question_count)
    _draw_objective_one_col(c, col_x=C.COL3_X, start_q=16, question_count=question_count)
    _draw_objective_one_col(c, col_x=C.COL4_X, start_q=31, question_count=question_count)


def _draw_objective_one_col(c, *, col_x: float, start_q: int, question_count: int) -> None:
    # header
    c.setFont("Helvetica-Bold", C.Q_HEADER_FONT_SIZE)
    header = f"{start_q} ~ {start_q + (C.Q_ROWS_PER_COL - 1)}"
    c.drawCentredString(col_x + C.COL_WIDTH / 2, C.PAGE_HEIGHT - C.MARGIN_Y - C.Q_HEADER_Y_PAD, header)

    # row geometry (fills down to bottom with no weird leftover)
    top, bottom = _question_area_y_bounds()
    row_gap = (top - bottom) / (C.Q_ROWS_PER_COL - 1)

    bx0 = _question_bubbles_start_x(col_x)

    c.setFont("Helvetica", C.Q_FONT_SIZE)

    y = top
    for idx in range(C.Q_ROWS_PER_COL):
        qnum = start_q + idx

        # only draw up to question_count; rest stays blank
        if qnum <= question_count:
            # number (left aligned)
            c.drawString(col_x + C.Q_LEFT_PAD, y - 2 * mm, str(qnum))
            # bubbles (right aligned)
            for k in range(C.Q_CHOICE_COUNT):
                c.circle(bx0 + k * C.Q_CHOICE_GAP, y, C.Q_BUBBLE_R)

        # separator after each 5 questions (except after last)
        if idx in (C.Q_GROUP_SIZE - 1, 2 * C.Q_GROUP_SIZE - 1):
            c.setLineWidth(0.6)
            c.line(col_x + C.Q_LEFT_PAD, y - (row_gap / 2), col_x + C.COL_WIDTH - C.Q_LEFT_PAD, y - (row_gap / 2))
            c.setLineWidth(0.8)

        y -= row_gap
