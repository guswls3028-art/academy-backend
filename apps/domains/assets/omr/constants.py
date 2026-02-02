from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.units import mm

# =========================
# PAGE
# =========================
PAGE_SIZE = landscape(A4)
PAGE_WIDTH, PAGE_HEIGHT = PAGE_SIZE

MARGIN_X = 12 * mm
MARGIN_Y = 12 * mm

# =========================
# GRID: LEFT 1 + RIGHT 3
# =========================
COL_COUNT = 4
COL_GAP = 6 * mm

COL_WIDTH = (PAGE_WIDTH - (MARGIN_X * 2) - (COL_GAP * (COL_COUNT - 1))) / COL_COUNT

COL1_X = MARGIN_X
COL2_X = COL1_X + COL_WIDTH + COL_GAP
COL3_X = COL2_X + COL_WIDTH + COL_GAP
COL4_X = COL3_X + COL_WIDTH + COL_GAP

# =========================
# LEFT COLUMN SECTIONS
# (left column is independent; just consumes its own width/height)
# =========================
LEFT_PAD = 3 * mm

LOGO_BOX_H = 28 * mm
EXAMINFO_BOX_H = 22 * mm
NAME_BOX_H = 18 * mm

# gap between left blocks
LEFT_BLOCK_GAP_1 = 6 * mm   # logo -> examinfo
LEFT_BLOCK_GAP_2 = 4 * mm   # examinfo -> name
LEFT_BLOCK_GAP_3 = 6 * mm   # name -> identifier

# =========================
# IDENTIFIER (8 digits, 0~9 bubbles)
# =========================
IDENT_TITLE_FONT_SIZE = 9
IDENT_NUM_FONT_SIZE = 8

IDENT_DIGITS = 8
IDENT_ROWS = 10  # 0~9
IDENT_BUBBLE_R = 2.4 * mm

# the identifier area auto-fills remaining height (computed in layout)
IDENT_DIGIT_RIGHT_PAD = 2 * mm
IDENT_EXTRA_RIGHT_GAP = 10 * mm  # same philosophy: labels left, marking right

# =========================
# OBJECTIVE QUESTIONS
# =========================
QUESTION_MAX = 45
ALLOWED_QUESTION_COUNTS = tuple(range(1, QUESTION_MAX + 1))

Q_FONT_SIZE = 9
Q_HEADER_FONT_SIZE = 9

Q_BUBBLE_R = 2.4 * mm
Q_CHOICE_COUNT = 5
Q_CHOICE_GAP = 9.0 * mm

Q_LEFT_PAD = 2 * mm
Q_RIGHT_PAD = 2 * mm

Q_ROWS_PER_COL = 15  # fixed: 1~15 / 16~30 / 31~45
Q_GROUP_SIZE = 5     # separator each 5

# question area vertical anchors (layout will fit exactly to bottom)
Q_HEADER_Y_PAD = 6 * mm    # header baseline from top margin
Q_TOP_PAD = 14 * mm        # top padding inside content area
Q_BOTTOM_PAD = 6 * mm      # bottom padding inside content area

# =========================
# LOGO UPLOAD VALIDATION
# =========================
ALLOWED_LOGO_CONTENT_TYPES = (
    "image/png",
    "image/jpeg",
    "image/jpg",
    "image/webp",
)
