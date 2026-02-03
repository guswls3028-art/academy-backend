from reportlab.lib.units import mm
from apps.domains.assets.omr import constants as C
from apps.domains.assets.omr.utils.draw_utils import draw_ellipse_bubble, draw_dashed_line

def draw(c, *, question_count, logo_reader=None, exam_title="모의고사", subject_round="전과목"):
    _draw_frames(c)
    _draw_info_column(c, logo_reader, exam_title, subject_round)
    _draw_objective_area(c, question_count)
    _draw_footer(c)

def _draw_frames(c):
    c.setLineWidth(C.LW_THICK)
    # 외곽 프레임
    c.rect(C.MARGIN_X, C.MARGIN_Y, C.PAGE_WIDTH - 2*C.MARGIN_X, C.PAGE_HEIGHT - 2*C.MARGIN_Y)
    # 좌측 분리선
    c.line(C.MARGIN_X + C.LEFT_COL_WIDTH, C.MARGIN_Y, C.MARGIN_X + C.LEFT_COL_WIDTH, C.PAGE_HEIGHT - C.MARGIN_Y)

def _draw_info_column(c, logo_reader, exam_title, subject_round):
    x_start = C.MARGIN_X + 4*mm
    y_top = C.PAGE_HEIGHT - C.MARGIN_Y - 5*mm

    # 1. Logo (No Border)
    if logo_reader:
        c.drawImage(logo_reader, x_start, y_top - C.LOGO_AREA_H + 2*mm, 
                    width=C.LEFT_COL_WIDTH - 8*mm, height=C.LOGO_AREA_H - 4*mm, 
                    preserveAspectRatio=True, anchor='sw', mask='auto')
    
    # 2. Exam Info
    info_y = y_top - C.LOGO_AREA_H - 5*mm
    c.setFont("Helvetica-Bold", 10)
    c.drawString(x_start, info_y, f"TITLE: {exam_title}")
    c.drawString(x_start, info_y - 8*mm, f"SUBJ: {subject_round}")
    
    # 3. Name Field
    name_y = info_y - 20*mm
    c.drawString(x_start, name_y, "NAME:")
    c.setLineWidth(C.LW_REG)
    c.line(x_start + 15*mm, name_y - 1*mm, C.MARGIN_X + C.LEFT_COL_WIDTH - 5*mm, name_y - 1*mm)

    # 4. Identifier (수험번호 0~9 세로)
    ident_y_base = name_y - 15*mm
    c.setFont("Helvetica-Bold", 9)
    c.drawString(x_start, ident_y_base, "IDENTIFIER")
    
    digit_w = (C.LEFT_COL_WIDTH - 12*mm) / C.IDENT_DIGITS
    for d in range(C.IDENT_DIGITS):
        dx = x_start + (d * digit_w)
        # 입력칸
        c.rect(dx, ident_y_base - 10*mm, digit_w - 1*mm, 8*mm)
        for n in range(10):
            dy = ident_y_base - 28*mm - (n * 6.2*mm)
            draw_ellipse_bubble(c, dx, dy, C.IDENT_BUBBLE_W, C.IDENT_BUBBLE_H, label=n, font_size=5)

def _draw_objective_area(c, question_count):
    start_x = C.MARGIN_X + C.LEFT_COL_WIDTH + C.COL_GAP/2
    for i, start_num in enumerate([1, 16, 31]):
        _draw_objective_column(c, start_x + (i * C.OBJECTIVE_COL_WIDTH), start_num, question_count)

def _draw_objective_column(c, x, start_num, question_count):
    y_start = C.PAGE_HEIGHT - C.MARGIN_Y - 15*mm
    row_h = 11.8*mm
    
    # Column Header
    c.setFillColorRGB(0.9, 0.9, 0.9)
    c.rect(x + 1*mm, y_start + 2*mm, C.OBJECTIVE_COL_WIDTH - 4*mm, 7*mm, fill=1)
    c.setFillColorRGB(0, 0, 0)
    c.setFont("Helvetica-Bold", 9)
    c.drawCentredString(x + C.OBJECTIVE_COL_WIDTH/2, y_start + 4.5*mm, f"{start_num} ~ {start_num+14}")

    for idx in range(C.Q_ROWS_PER_COL):
        q_num = start_num + idx
        curr_y = y_start - (idx * row_h) - 5*mm
        
        # 45번까지 틀은 유지, 데이터는 선택적으로 출력
        if q_num <= question_count:
            c.setFont("Helvetica-Bold", 9)
            c.drawString(x + 2*mm, curr_y + 1*mm, f"{q_num:02d}")
            for b in range(1, 6):
                bx = x + 12*mm + (b-1) * C.CHOICE_GAP
                draw_ellipse_bubble(c, bx, curr_y, C.BUBBLE_W, C.BUBBLE_H, label=b, font_size=6)
        
        # 5문항 가이드 점선
        if (idx + 1) % 5 == 0 and idx < 14:
            draw_dashed_line(c, x + 2*mm, curr_y - 4*mm, x + C.OBJECTIVE_COL_WIDTH - 4*mm, curr_y - 4*mm)

def _draw_footer(c):
    c.setFont("Helvetica-Bold", 9)
    # 우측 하단 정렬
    c.drawRightString(C.PAGE_WIDTH - C.MARGIN_X, C.MARGIN_Y + 4*mm, C.FOOTER_TEXT)