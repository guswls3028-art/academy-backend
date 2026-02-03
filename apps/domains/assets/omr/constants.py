# apps/domains/assets/omr/constants.py
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.units import mm

# ====================================================================
# PAGE & GRID SYSTEM
# ====================================================================
PAGE_SIZE = landscape(A4)
PAGE_WIDTH, PAGE_HEIGHT = PAGE_SIZE

MARGIN_X = 12 * mm
MARGIN_Y = 12 * mm

COL_GAP = 8 * mm
LEFT_COL_WIDTH = 68 * mm
RIGHT_CONTENT_WIDTH = PAGE_WIDTH - (2 * MARGIN_X) - LEFT_COL_WIDTH - COL_GAP
OBJECTIVE_COL_WIDTH = RIGHT_CONTENT_WIDTH / 3

# ====================================================================
# OPTICAL MARK RECOGNITION (BUBBLE SPEC)
# ====================================================================
BUBBLE_W = 4.5 * mm  
BUBBLE_H = 3.0 * mm
CHOICE_GAP = 5.5 * mm

IDENT_BUBBLE_W = 3.8 * mm
IDENT_BUBBLE_H = 2.6 * mm
IDENT_DIGITS = 8
IDENT_ROWS = 10

# ====================================================================
# LAYOUT CONSTANTS
# ====================================================================
LOGO_AREA_H = 25 * mm
INFO_AREA_H = 35 * mm
IDENT_TITLE_H = 10 * mm

QUESTION_MAX = 45
Q_ROWS_PER_COL = 15
ALLOWED_QUESTION_COUNTS = tuple(range(1, QUESTION_MAX + 1))

LW_THICK = 1.2
LW_REG = 0.7
LW_THIN = 0.4

FOOTER_TEXT = "※ 컴퓨터용 사인펜을 사용하여 마킹하십시오. (수정 시 수정테이프 사용 가능)"