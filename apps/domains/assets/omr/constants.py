from reportlab.lib.units import mm

def draw_ellipse_bubble(c, x, y, w, h, label="", font_size=6):
    """
    정밀한 타원형 버블 생성. 
    x, y는 타원의 좌측 하단 시작점.
    """
    c.setLineWidth(0.6)
    c.ellipse(x, y, x + w, y + h, stroke=1, fill=0)
    if label:
        c.setFont("Helvetica", font_size)
        # 타원 중앙 정렬 계산
        c.drawCentredString(x + w/2, y + (h/2) - (font_size/2.5), str(label))

def draw_dashed_line(c, x1, y1, x2, y2, dash=(1, 1)):
    c.setDash(dash[0], dash[1])
    c.setLineWidth(0.4)
    c.line(x1, y1, x2, y2)
    c.setDash() # 리셋