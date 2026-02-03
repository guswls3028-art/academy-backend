# PATH: tools/generate_omr_preview.py
from __future__ import annotations

import os
import sys
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)


from reportlab.pdfgen import canvas

# -----------------------------------------------------------------------------
# BOOTSTRAP: fix "No module named 'apps'"
# -----------------------------------------------------------------------------
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from apps.domains.assets.omr import constants as C
from apps.domains.assets.omr.layouts.objective import draw as draw_objective
from apps.domains.assets.omr.utils.debug_grid import draw_debug_grid

DEBUG_GRID = os.getenv("OMR_DEBUG_GRID", "0") == "1"  # OMR_DEBUG_GRID=1 일 때만


def generate_preview(question_count: int, out_path: str):
    c = canvas.Canvas(out_path, pagesize=C.PAGE_SIZE)

    draw_objective(c, question_count=question_count)

    if DEBUG_GRID:
        draw_debug_grid(c)

    c.showPage()
    c.save()


def main():
    out_dir = os.path.join(ROOT, "_omr_debug")
    os.makedirs(out_dir, exist_ok=True)

    out_path = os.path.join(out_dir, "omr_preview_final_q45.pdf")
    generate_preview(45, out_path)
    print(f"PDF : {out_path}")


if __name__ == "__main__":
    main()
