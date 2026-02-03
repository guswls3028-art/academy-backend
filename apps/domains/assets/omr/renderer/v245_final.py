# ⚠️ SSOT
# ⚠️ DO NOT MODIFY
# ⚠️ Source of Truth for OMR rendering

# apps/domains/assets/omr/renderer/v245_final.py

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Tuple

from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.units import mm
from reportlab.lib.colors import Color


# =============================================================================
# OMR v2.45 FINAL — SINGLE FILE RENDERER
# - No Django imports
# - No "apps" imports
# - Grid-based (120 x 84)
# - Optional debug grid overlay
# =============================================================================

PAGE_SIZE = landscape(A4)
PAGE_W, PAGE_H = PAGE_SIZE

GRID_COLS = 120
GRID_ROWS = 84

GRID_UNIT_X_MM = (PAGE_W / mm) / GRID_COLS
GRID_UNIT_Y_MM = (PAGE_H / mm) / GRID_ROWS


def gx(v: float) -> float:
    return v * GRID_UNIT_X_MM * mm


def gy(v: float) -> float:
    return v * GRID_UNIT_Y_MM * mm


def gw(v: float) -> float:
    return v * GRID_UNIT_X_MM * mm


def gh(v: float) -> float:
    return v * GRID_UNIT_Y_MM * mm


@dataclass(frozen=True)
class R:
    """Grid-rect"""
    x: float
    y: float
    w: float
    h: float

    @property
    def X(self) -> float:
        return gx(self.x)

    @property
    def Y(self) -> float:
        return gy(self.y)

    @property
    def W(self) -> float:
        return gw(self.w)

    @property
    def H(self) -> float:
        return gh(self.h)

    def inset(self, dx: float = 0.0, dy: float = 0.0) -> "R":
        return R(self.x + dx, self.y + dy, self.w - 2 * dx, self.h - 2 * dy)


# =============================================================================
# Drawing primitives
# =============================================================================
def _set_font(c: canvas.Canvas, size: float):
    # NOTE: Do NOT use Korean text unless you register a CJK font.
    # For stability, we keep labels in ASCII only.
    c.setFont("Helvetica-Bold", size)


def round_rect(c: canvas.Canvas, x: float, y: float, w: float, h: float, r: float, lw: float = 1.0):
    c.setLineWidth(lw)
    c.roundRect(x, y, w, h, r, stroke=1, fill=0)


def hline(c: canvas.Canvas, x1: float, y: float, x2: float, lw: float = 0.3):
    c.setLineWidth(lw)
    c.line(x1, y, x2, y)


def vline(c: canvas.Canvas, x: float, y1: float, y2: float, lw: float = 0.3):
    c.setLineWidth(lw)
    c.line(x, y1, x, y2)


def oval_bubble(c: canvas.Canvas, x: float, y: float, w: float, h: float, outer_lw: float = 0.65, inner_lw: float = 0.40, inset: float = 0.20 * mm):
    # Outer
    c.setLineWidth(outer_lw)
    c.ellipse(x, y, x + w, y + h)
    # Inner
    c.setLineWidth(inner_lw)
    c.ellipse(x + inset, y + inset, x + w - inset, y + h - inset)


# =============================================================================
# Debug grid overlay
# =============================================================================
def draw_debug_grid(c: canvas.Canvas, major_every: int = 6, minor_lw: float = 0.12, major_lw: float = 0.28):
    minor_color = Color(0.75, 0.75, 0.75, alpha=0.55)
    major_color = Color(0.55, 0.55, 0.55, alpha=0.75)

    # Vertical
    for x in range(GRID_COLS + 1):
        is_major = (x % major_every == 0)
        c.setStrokeColor(major_color if is_major else minor_color)
        c.setLineWidth(major_lw if is_major else minor_lw)
        c.line(gx(x), gy(0), gx(x), gy(GRID_ROWS))

    # Horizontal
    for y in range(GRID_ROWS + 1):
        is_major = (y % major_every == 0)
        c.setStrokeColor(major_color if is_major else minor_color)
        c.setLineWidth(major_lw if is_major else minor_lw)
        c.line(gx(0), gy(y), gx(GRID_COLS), gy(y))

    # Reset stroke
    c.setStrokeColor(Color(0, 0, 0, alpha=1))


# =============================================================================
# Layout spec (v2.45 느낌: 카드 3개 + 간격 + 내부 디테일 유지)
# =============================================================================
# Page frame margin (grid units)
MARGIN_L = 3
MARGIN_R = 3
MARGIN_T = 3
MARGIN_B = 3

FRAME_LW = 1.2
DIVIDER_LW = 1.0

# Left area
LEFT_W = 28  # left column width in grid units
LEFT_AREA = R(MARGIN_L, MARGIN_B, LEFT_W, GRID_ROWS - (MARGIN_T + MARGIN_B))

# Logo (top-left remain area)
LOGO_BOX = R(MARGIN_L + 2, GRID_ROWS - MARGIN_T - 18, 16, 16)

# Bottom-aligned left info block region (we anchor to bottom of LEFT_AREA)
LEFT_INFO_INSET_X = 2.0
LEFT_INFO_INSET_Y = 2.0

# Name/Phone boxes
NAME_BOX_H = 4.2
IDENT_BOX_H = 4.2

# Identifier bubble card
IDENT_CARD_H = 22.0
IDENT_BUBBLE_ROWS = 9
IDENT_DIGITS = 8

# Right answer area cards
RIGHT_X0 = MARGIN_L + LEFT_W  # divider x
RIGHT_AREA = R(RIGHT_X0, MARGIN_B, GRID_COLS - (MARGIN_R + RIGHT_X0), GRID_ROWS - (MARGIN_T + MARGIN_B))

CARD_W = 24.0      # card width (grid)
CARD_GAP = 3.2     # gap between cards (grid)
CARDS_X = [
    RIGHT_X0 + 3.2,
    RIGHT_X0 + 3.2 + CARD_W + CARD_GAP,
    RIGHT_X0 + 3.2 + (CARD_W + CARD_GAP) * 2,
]

# Card vertical placement: center inside RIGHT_AREA
CARD_H = 68.0
CARD_TOP_Y = MARGIN_B + ((RIGHT_AREA.h - CARD_H) / 2)

# Card inner padding
PAD_X = 1.6
PAD_T = 2.2
PAD_B = 2.0

# Header
HEADER_H = 5.0

# Row config
ROWS_PER_CARD = 15
GROUP_SIZE = 5

# Number column width (tighten 번호열~버블1 gap)
NUM_COL_W = 3.2

# Bubble geometry (keep “vertical oval” vibe)
BUBBLE_W = 2.0
BUBBLE_H = 3.1
BUBBLE_GAP = 0.9

# Vertical separator shift: 0.5 bubble width (as requested)
SEP_SHIFT = BUBBLE_W * 0.5

# Line weights
CARD_OUTER_LW = 1.0
VLINE_MID_LW = 0.55
VLINE_THIN_LW = 0.28
GROUP_RULE_LW = 0.35
HEADER_RULE_LW = 0.60


def draw_page_frame(c: canvas.Canvas):
    c.setLineWidth(FRAME_LW)
    c.rect(gx(MARGIN_L), gy(MARGIN_B), gx(GRID_COLS - MARGIN_L - MARGIN_R), gy(GRID_ROWS - MARGIN_T - MARGIN_B))
    c.setLineWidth(DIVIDER_LW)
    c.line(gx(RIGHT_X0), gy(MARGIN_B), gx(RIGHT_X0), gy(GRID_ROWS - MARGIN_T))


def draw_left_side(c: canvas.Canvas):
    # Logo placeholder (top-left empty box)
    round_rect(c, LOGO_BOX.X, LOGO_BOX.Y, LOGO_BOX.W, LOGO_BOX.H, r=gw(1.0), lw=0.9)

    # Bottom-aligned block inside LEFT_AREA:
    # Layout order (bottom-up):
    # [ident bubble card]
    # gap
    # [id text box (Phone number text box)]
    # gap
    # [name text box]
    area = LEFT_AREA.inset(LEFT_INFO_INSET_X, LEFT_INFO_INSET_Y)

    gap = 2.2

    ident_card = R(area.x, area.y, area.w, IDENT_CARD_H)  # at very bottom
    ident_text = R(area.x, ident_card.y + ident_card.h + gap, area.w, IDENT_BOX_H)
    name_text = R(area.x, ident_text.y + ident_text.h + gap, area.w, NAME_BOX_H)

    # Name box
    _set_font(c, 9)
    c.drawString(name_text.X, name_text.Y + name_text.H + gh(0.8), "NAME")
    round_rect(c, name_text.X, name_text.Y, name_text.W, name_text.H, r=gw(1.0), lw=1.0)

    # Phone box (replacing exam ID)
    _set_font(c, 9)
    c.drawString(ident_text.X, ident_text.Y + ident_text.H + gh(0.8), "PHONE")
    round_rect(c, ident_text.X, ident_text.Y, ident_text.W, ident_text.H, r=gw(1.0), lw=1.0)

    # Split lines (8 digits) in phone box
    digit_w = ident_text.W / IDENT_DIGITS
    for i in range(1, IDENT_DIGITS):
        x = ident_text.X + digit_w * i
        vline(c, x, ident_text.Y, ident_text.Y + ident_text.H, lw=0.30)

    # Bubble card
    round_rect(c, ident_card.X, ident_card.Y, ident_card.W, ident_card.H, r=gw(1.0), lw=1.0)

    # Split lines in bubble card
    for i in range(1, IDENT_DIGITS):
        x = ident_card.X + digit_w * i
        vline(c, x, ident_card.Y, ident_card.Y + ident_card.H, lw=0.22)

    # Bubble rows (9)
    # Place bubbles centered vertically in ident_card with small top/bottom padding
    pad_y = 2.0
    grid_h = ident_card.h - 2 * pad_y
    row_gap = grid_h / IDENT_BUBBLE_ROWS

    bubble_w = gw(1.2)
    bubble_h = gh(1.8)

    for r in range(IDENT_BUBBLE_ROWS):
        cy = ident_card.y + pad_y + (IDENT_BUBBLE_ROWS - 1 - r) * row_gap + (row_gap - (bubble_h / mm) / GRID_UNIT_Y_MM) / 2
        y = gy(cy)  # convert grid to page
        # row label left of card
        _set_font(c, 7.5)
        c.drawString(ident_card.X - gw(1.8), y + bubble_h * 0.25, str(r + 1))

        for col in range(IDENT_DIGITS):
            # bubble x centered in each digit column
            bx_center = ident_card.X + digit_w * col + digit_w / 2
            x = bx_center - bubble_w / 2
            oval_bubble(c, x, y, bubble_w, bubble_h, outer_lw=0.65, inner_lw=0.40, inset=0.20 * mm)


def _card_rect(idx: int) -> R:
    return R(CARDS_X[idx], CARD_TOP_Y, CARD_W, CARD_H)


def draw_right_cards(c: canvas.Canvas, question_count: int = 45):
    # Optional small label (ASCII to avoid CJK font dependency)
    _set_font(c, 12)
    c.drawString(gx(RIGHT_X0 + 3.2), gy(GRID_ROWS - MARGIN_T - 4.0), "ANSWER AREA")

    q = 1
    ranges = [(1, 15), (16, 30), (31, 45)]

    for i, (start, end) in enumerate(ranges):
        if start > question_count:
            break

        card = _card_rect(i)
        inner = card.inset(PAD_X, PAD_B)
        inner = R(inner.x, inner.y, inner.w, inner.h - (PAD_T + PAD_B))  # keep same idea

        # Outer
        round_rect(c, card.X, card.Y, card.W, card.H, r=gw(1.0), lw=CARD_OUTER_LW)

        # Header band line + label
        header_y = card.Y + card.H - gh(HEADER_H)
        hline(c, card.X + gw(2.5), header_y, card.X + card.W - gw(2.5), lw=HEADER_RULE_LW)
        _set_font(c, 9)
        c.drawCentredString(card.X + card.W / 2, header_y - gh(2.1), f"{start} - {end}")

        # Compute row area (below header, inside card)
        rows_top = header_y - gh(0.8)
        rows_bottom = card.Y + gh(2.0)
        rows_h = rows_top - rows_bottom
        row_h = rows_h / ROWS_PER_CARD

        # Vertical separator (number | bubbles) shifted by 0.5 bubble
        split_x = card.X + gw(NUM_COL_W) + gw(SEP_SHIFT)

        # Draw full-height separator inside rows area
        vline(c, split_x, rows_bottom, rows_top, lw=VLINE_MID_LW)

        # Bubble start X (tight)
        bubble_start_x = split_x + gw(0.9)  # small breathing room

        # Thin vertical lines aligned to bubble centers
        for k in range(5):
            cx = bubble_start_x + gw(k * (BUBBLE_W + BUBBLE_GAP) + (BUBBLE_W / 2))
            vline(c, cx, rows_bottom, rows_top, lw=VLINE_THIN_LW)

        for row in range(ROWS_PER_CARD):
            cur_q = start + row
            if cur_q > min(end, question_count):
                break

            # Bubble Y centered in row
            y = rows_top - (row + 1) * row_h + (row_h - gh(BUBBLE_H)) / 2

            # Question number (tight to left)
            _set_font(c, 9)
            c.drawString(card.X + gw(0.8), y + gh(0.85), str(cur_q))

            # Bubbles
            for k in range(5):
                bx = bubble_start_x + gw(k * (BUBBLE_W + BUBBLE_GAP))
                oval_bubble(c, bx, y, gw(BUBBLE_W), gh(BUBBLE_H), outer_lw=0.65, inner_lw=0.40, inset=0.20 * mm)

                _set_font(c, 7)
                c.drawCentredString(bx + gw(BUBBLE_W) / 2, y + gh(BUBBLE_H) / 2 - gh(0.30), str(k + 1))

            # Group rule (every 5, excluding last)
            if (row + 1) % GROUP_SIZE == 0 and row != ROWS_PER_CARD - 1:
                ry = rows_top - (row + 1) * row_h
                hline(c, card.X + gw(2.0), ry, card.X + card.W - gw(2.0), lw=GROUP_RULE_LW)

        q += ROWS_PER_CARD


def render(out_path: str, *, question_count: int = 45, debug_grid: bool = False):
    c = canvas.Canvas(out_path, pagesize=PAGE_SIZE)

    # Frame + divider
    draw_page_frame(c)

    # Left
    draw_left_side(c)

    # Right
    draw_right_cards(c, question_count=question_count)

    # Debug overlay on top (optional)
    if debug_grid:
        draw_debug_grid(c)

    c.showPage()
    c.save()


def main():
    # Defaults
    out_path = os.getenv("OMR_OUT", r"_omr_debug\omr_v245_final.pdf")
    qc = int(os.getenv("OMR_QC", "45"))

    # Debug grid flag:
    # - env: OMR_DEBUG_GRID=1
    # - or CLI: python tools/render_omr_v245_final.py --grid
    debug_grid = (os.getenv("OMR_DEBUG_GRID", "0") == "1")

    import sys
    if "--grid" in sys.argv:
        debug_grid = True

    render(out_path, question_count=qc, debug_grid=debug_grid)
    print(f"[OK] generated: {out_path} (qc={qc}, grid={debug_grid})")


if __name__ == "__main__":
    main()
