# apps/domains/assets/omr/constants.py
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.units import mm

# -----------------------------
# Page / Layout (Single Source of Truth)
# -----------------------------
PAGE_SIZE = landscape(A4)  # 시험지 1은 항상 A4 가로
PAGE_WIDTH, PAGE_HEIGHT = PAGE_SIZE

MARGIN_X = 14 * mm
MARGIN_Y = 14 * mm

# 3단 레이아웃
COL_COUNT = 3
COL_GAP = 6 * mm
COL_WIDTH = (PAGE_WIDTH - (MARGIN_X * 2) - (COL_GAP * (COL_COUNT - 1))) / COL_COUNT

COL1_X = MARGIN_X
COL2_X = MARGIN_X + (COL_WIDTH + COL_GAP) * 1
COL3_X = MARGIN_X + (COL_WIDTH + COL_GAP) * 2

# 로고 영역 (영역 1 상단)
LOGO_BOX_HEIGHT = 22 * mm
LOGO_BOX_Y = PAGE_HEIGHT - MARGIN_Y - LOGO_BOX_HEIGHT

# 식별자 영역 (영역 1 하단)
IDENT_TITLE_FONT_SIZE = 9
IDENT_NUM_FONT_SIZE = 8
IDENT_DIGITS = 8
IDENT_ROWS = 10  # 0~9
IDENT_ROW_GAP = 5.2 * mm
IDENT_COL_GAP = 10.5 * mm
IDENT_BUBBLE_R = 2.6 * mm

IDENT_AREA_BOTTOM = MARGIN_Y + 10 * mm
IDENT_AREA_TOP = IDENT_AREA_BOTTOM + (IDENT_ROWS - 1) * IDENT_ROW_GAP + 18 * mm  # 타이틀/여유 포함

# 객관식 버블/텍스트
Q_FONT_SIZE = 10
Q_BUBBLE_R = 2.6 * mm
Q_CHOICE_COUNT = 5
Q_CHOICE_GAP = 9.5 * mm
Q_LEFT_PAD = 2 * mm  # 문항번호 좌측 패딩
Q_RIGHT_PAD = 2 * mm  # 버블 우측 패딩

# 객관식 시작/끝 Y
Q_AREA_TOP = PAGE_HEIGHT - MARGIN_Y - 10 * mm
Q_AREA_BOTTOM = MARGIN_Y + 14 * mm

# 문항수별 세로 간격 (문항 수 적을수록 더 넉넉)
ROW_GAP_BY_COUNT = {
    30: 11.0 * mm,
    20: 14.0 * mm,
    10: 20.0 * mm,
}

# 문항수별 컬럼 분배(영역2/3)
# - 철학: 레이아웃 동일, 밀도만 달라짐
DISTRIBUTION_BY_COUNT = {
    30: (15, 15),
    20: (10, 10),
    10: (5, 5),
}

ALLOWED_QUESTION_COUNTS = (10, 20, 30)

# 로고 파일 타입(요청 검증용)
ALLOWED_LOGO_CONTENT_TYPES = (
    "image/png",
    "image/jpeg",
    "image/jpg",
    "image/webp",
)
