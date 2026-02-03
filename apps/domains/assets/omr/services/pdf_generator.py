from io import BytesIO
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader
from apps.domains.assets.omr import constants as C
from apps.domains.assets.omr.layouts.objective_v2_45 import draw as draw_objective

def generate_objective_pdf(*, question_count, logo_file=None, exam_title="", subject_round=""):
    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=C.PAGE_SIZE)
    
    logo_reader = None
    if logo_file:
        logo_file.seek(0)
        logo_reader = ImageReader(logo_file)

    # 상용 레벨 레이아웃 실행
    draw_objective(
        c, 
        question_count=question_count, 
        logo_reader=logo_reader,
        exam_title=exam_title,
        subject_round=subject_round
    )
    
    c.showPage()
    c.save()
    buf.seek(0)
    return buf.read()