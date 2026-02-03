# PATH: tools/generate_grid_overlay_english.py
"""
기존 PDF의 '영어 영역(0-based index = 2)' 페이지만
A4 120x84 DEBUG GRID를 투명 오버레이로 씌운다.
"""
import os
import sys
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

from io import BytesIO
from pathlib import Path

from PyPDF2 import PdfReader, PdfWriter
from reportlab.pdfgen import canvas

# === 네 프로젝트 기준 grid 유틸 ===
from apps.domains.assets.omr.grid import GRID_COLS, GRID_ROWS, gx, gy
from reportlab.lib.colors import Color

# -------------------------------
# 설정
# -------------------------------
SOURCE_PDF = Path("_omr_debug/omr_preview_final_q45.pdf")
OUTPUT_PDF = Path("_omr_debug/omr_preview_english_with_grid.pdf")

TARGET_PAGE_INDEX = 2  # ✅ 영어 (0-based 기준)
MAJOR_EVERY = 6

MINOR_LW = 0.15
MAJOR_LW = 0.35

MINOR_COLOR = Color(0.75, 0.75, 0.75, alpha=0.6)
MAJOR_COLOR = Color(0.55, 0.55, 0.55, alpha=0.8)


def build_grid_overlay_pdf(page_width, page_height) -> BytesIO:
    """
    ReportLab으로 '투명 grid 페이지만' 만든다.
    """
    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=(page_width, page_height))

    # Vertical lines
    for x in range(GRID_COLS + 1):
        is_major = (x % MAJOR_EVERY == 0)
        c.setStrokeColor(MAJOR_COLOR if is_major else MINOR_COLOR)
        c.setLineWidth(MAJOR_LW if is_major else MINOR_LW)
        c.line(
            gx(x),
            gy(0),
            gx(x),
            gy(GRID_ROWS),
        )

    # Horizontal lines
    for y in range(GRID_ROWS + 1):
        is_major = (y % MAJOR_EVERY == 0)
        c.setStrokeColor(MAJOR_COLOR if is_major else MINOR_COLOR)
        c.setLineWidth(MAJOR_LW if is_major else MINOR_LW)
        c.line(
            gx(0),
            gy(y),
            gx(GRID_COLS),
            gy(y),
        )

    c.showPage()
    c.save()
    buf.seek(0)
    return buf


def main():
    if not SOURCE_PDF.exists():
        raise FileNotFoundError(SOURCE_PDF)

    reader = PdfReader(str(SOURCE_PDF))
    writer = PdfWriter()

    if TARGET_PAGE_INDEX >= len(reader.pages):
        raise IndexError(
            f"TARGET_PAGE_INDEX={TARGET_PAGE_INDEX}, "
            f"but total pages={len(reader.pages)}"
        )

    # 기준 페이지 크기
    base_page = reader.pages[TARGET_PAGE_INDEX]
    page_width = float(base_page.mediabox.width)
    page_height = float(base_page.mediabox.height)

    # grid overlay 페이지 생성
    grid_pdf_buf = build_grid_overlay_pdf(page_width, page_height)
    grid_reader = PdfReader(grid_pdf_buf)
    grid_page = grid_reader.pages[0]

    # 모든 페이지 복사하되, 영어 페이지만 overlay
    for i, page in enumerate(reader.pages):
        if i == TARGET_PAGE_INDEX:
            page.merge_page(grid_page)
        writer.add_page(page)

    with open(OUTPUT_PDF, "wb") as f:
        writer.write(f)

    print(f"✅ 영어(페이지 {TARGET_PAGE_INDEX})에만 grid 적용 완료")
    print(f"📄 OUTPUT: {OUTPUT_PDF.resolve()}")


if __name__ == "__main__":
    main()
