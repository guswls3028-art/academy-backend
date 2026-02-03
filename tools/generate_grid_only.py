# tools/generate_grid_only.py
# tools/generate_grid_only.py
import os
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)


from reportlab.pdfgen import canvas
from apps.domains.assets.omr import constants as C
from apps.domains.assets.omr.utils.debug_grid import draw_debug_grid

OUT_PATH = "_omr_debug/grid_only.pdf"


def main():
    c = canvas.Canvas(OUT_PATH, pagesize=C.PAGE_SIZE)

    # 🔲 GRID ONLY
    draw_debug_grid(c)

    c.showPage()
    c.save()
    print(f"✅ GRID ONLY PDF 생성 완료: {OUT_PATH}")


if __name__ == "__main__":
    main()
