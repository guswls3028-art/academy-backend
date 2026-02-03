# tools/generate_grid_overlay.py
import os
import sys
from io import BytesIO

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

from reportlab.pdfgen import canvas
from PyPDF2 import PdfReader, PdfWriter

from apps.domains.assets.omr import constants as C
from apps.domains.assets.omr.utils.debug_grid import draw_debug_grid

SOURCE_PDF = "_omr_debug/omr_preview_final_q45.pdf"
OUT_PDF = "_omr_debug/omr_preview_with_grid.pdf"


def make_grid_pdf() -> BytesIO:
    """
    Grid-only transparent PDF (same page size)
    """
    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=C.PAGE_SIZE)

    draw_debug_grid(
        c,
        major_every=6,
        minor_lw=0.15,
        major_lw=0.35,
    )

    c.showPage()
    c.save()
    buf.seek(0)
    return buf


def main():
    # 1️⃣ 원본 PDF
    reader = PdfReader(SOURCE_PDF)
    base_page = reader.pages[0]

    # 2️⃣ Grid PDF
    grid_pdf = make_grid_pdf()
    grid_reader = PdfReader(grid_pdf)
    grid_page = grid_reader.pages[0]

    # 3️⃣ Merge (grid가 위)
    base_page.merge_page(grid_page)

    # 4️⃣ Save
    writer = PdfWriter()
    writer.add_page(base_page)

    with open(OUT_PDF, "wb") as f:
        writer.write(f)

    print(f"✅ GRID OVERLAY PDF 생성 완료: {OUT_PDF}")


if __name__ == "__main__":
    main()
