# apps/domains/assets/omr/layouts/objective_v1_20.py
from reportlab.lib.units import mm
from apps.domains.assets.omr import constants as C


def draw(c, *, logo_reader=None) -> None:
    _draw_common(c, logo_reader=logo_reader, question_count=20)
    _draw_questions(c, question_count=20)


def _draw_common(c, *, logo_reader, question_count: int) -> None:
    if logo_reader is not None:
        c.drawImage(
            logo_reader,
            C.COL1_X,
            C.LOGO_BOX_Y,
            width=C.COL_WIDTH,
            height=C.LOGO_BOX_HEIGHT,
            preserveAspectRatio=True,
            mask="auto",
        )

    title_y = C.IDENT_AREA_TOP - 6 * mm
    c.setFont("Helvetica", C.IDENT_TITLE_FONT_SIZE)
    c.drawString(C.COL1_X, title_y, "휴대폰번호 (010 제외)")

    digit_start_x = C.COL1_X
    bubble_right_edge = C.COL1_X + C.COL_WIDTH - C.Q_RIGHT_PAD
    total_digits_width = (C.IDENT_DIGITS - 1) * C.IDENT_COL_GAP
    digits_left_x = bubble_right_edge - total_digits_width - 10 * mm

    c.setFont("Helvetica", C.IDENT_NUM_FONT_SIZE)
    for n in range(10):
        y = (C.IDENT_AREA_BOTTOM + (9 - n) * C.IDENT_ROW_GAP)
        c.drawString(digit_start_x, y - 2 * mm, str(n))
        for d in range(C.IDENT_DIGITS):
            x = digits_left_x + d * C.IDENT_COL_GAP
            c.circle(x, y, C.IDENT_BUBBLE_R)

    c.setLineWidth(0.4)
    c.line(C.COL1_X, C.IDENT_AREA_TOP, C.COL1_X + C.COL_WIDTH, C.IDENT_AREA_TOP)


def _draw_questions(c, *, question_count: int) -> None:
    left_count, right_count = C.DISTRIBUTION_BY_COUNT[question_count]
    row_gap = C.ROW_GAP_BY_COUNT[question_count]

    def bubbles_start_x(col_x: float) -> float:
        right_edge = col_x + C.COL_WIDTH - C.Q_RIGHT_PAD
        total_choice_width = (C.Q_CHOICE_COUNT - 1) * C.Q_CHOICE_GAP
        return right_edge - total_choice_width

    c.setFont("Helvetica", C.Q_FONT_SIZE)

    x2 = C.COL2_X
    y = C.Q_AREA_TOP
    start_bx2 = bubbles_start_x(x2)
    for i in range(1, left_count + 1):
        c.drawString(x2 + C.Q_LEFT_PAD, y - 2 * mm, str(i))
        for k in range(C.Q_CHOICE_COUNT):
            c.circle(start_bx2 + k * C.Q_CHOICE_GAP, y, C.Q_BUBBLE_R)
        y -= row_gap

    x3 = C.COL3_X
    y = C.Q_AREA_TOP
    start_bx3 = bubbles_start_x(x3)
    for i in range(left_count + 1, left_count + right_count + 1):
        c.drawString(x3 + C.Q_LEFT_PAD, y - 2 * mm, str(i))
        for k in range(C.Q_CHOICE_COUNT):
            c.circle(start_bx3 + k * C.Q_CHOICE_GAP, y, C.Q_BUBBLE_R)
        y -= row_gap
