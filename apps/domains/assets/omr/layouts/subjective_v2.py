from __future__ import annotations

from reportlab.lib.units import mm

from apps.domains.assets.omr import constants as C


def draw(c, *, line_count: int = 10, title: str = "서술형 답안지") -> None:
    """
    Back page (Subjective)
    - label left aligned
    - writing area right aligned
    - fixed A4 landscape, black&white
    """
    # frame
    c.setLineWidth(0.8)
    c.rect(
        C.MARGIN_X,
        C.MARGIN_Y,
        C.PAGE_WIDTH - 2 * C.MARGIN_X,
        C.PAGE_HEIGHT - 2 * C.MARGIN_Y,
    )

    c.setFont("Helvetica-Bold", 14)
    c.drawString(C.MARGIN_X, C.PAGE_HEIGHT - C.MARGIN_Y - 18, title)

    c.setFont("Helvetica", 10)
    top = C.PAGE_HEIGHT - C.MARGIN_Y - 32
    bottom = C.MARGIN_Y + 10 * mm

    # distribute evenly
    gap = (top - bottom) / max(1, line_count)

    y = top
    for i in range(1, line_count + 1):
        c.setFont("Helvetica-Bold", 10)
        c.drawString(C.MARGIN_X, y, f"{i}.")  # label left

        # writing line right
        c.setLineWidth(0.6)
        c.line(C.MARGIN_X + 12 * mm, y, C.PAGE_WIDTH - C.MARGIN_X, y)
        c.setLineWidth(0.8)

        y -= gap
