from reportlab.lib.units import mm
from apps.domains.assets.omr import constants as C

def draw(c, *, line_count=10, title="서술형 답안지"):
    """
    시험지 뒷면: 실제 대형 학원에서 사용하는 서술형/노트 영역 레이아웃
    """
    # 프레임
    c.setLineWidth(C.LW_THICK)
    c.rect(C.MARGIN_X, C.MARGIN_Y, C.PAGE_WIDTH - 2*C.MARGIN_X, C.PAGE_HEIGHT - 2*C.MARGIN_Y)

    # 타이틀
    c.setFont("Helvetica-Bold", 14)
    c.drawString(C.MARGIN_X + 5*mm, C.PAGE_HEIGHT - C.MARGIN_Y - 15*mm, title)

    # 작성 라인 렌더링
    top = C.PAGE_HEIGHT - C.MARGIN_Y - 30*mm
    bottom = C.MARGIN_Y + 15*mm
    gap = (top - bottom) / max(1, line_count)

    c.setLineWidth(C.LW_THIN)
    for i in range(line_count + 1):
        y = top - (i * gap)
        c.line(C.MARGIN_X + 5*mm, y, C.PAGE_WIDTH - C.MARGIN_X - 5*mm, y)
        if i < line_count:
            c.setFont("Helvetica-Bold", 10)
            c.drawString(C.MARGIN_X + 7*mm, y - 7*mm, f"{i+1}.")