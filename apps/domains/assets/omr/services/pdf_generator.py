from __future__ import annotations

from io import BytesIO
from typing import Optional

from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader

from apps.domains.assets.omr import constants as C
from apps.domains.assets.omr.layouts.objective_v2_45 import draw as draw_objective
from apps.domains.assets.omr.layouts.subjective_v2 import draw as draw_subjective


class LogoValidationError(Exception):
    pass


def _build_logo_reader(logo_file) -> Optional[ImageReader]:
    if logo_file is None:
        return None
    try:
        try:
            logo_file.seek(0)
        except Exception:
            pass
        reader = ImageReader(logo_file)
        _ = reader.getSize()
        return reader
    except Exception as e:
        raise LogoValidationError("logo must be a valid image file") from e


def generate_objective_pdf(
    *,
    question_count: int,
    logo_file=None,
    exam_title: str = "3월 모의고사",
    subject_round: str = "수학 (1회)",
) -> bytes:
    """
    Generates 2-page PDF:
      - Page1: objective OMR (new agreed layout)
      - Page2: subjective lines (simple back page)
    """
    if question_count not in C.ALLOWED_QUESTION_COUNTS:
        raise ValueError("invalid question_count")

    logo_reader = _build_logo_reader(logo_file)

    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=C.PAGE_SIZE)

    # page 1
    draw_objective(
        c,
        question_count=question_count,
        logo_reader=logo_reader,
        exam_title=exam_title,
        subject_round=subject_round,
    )
    c.showPage()

    # page 2
    draw_subjective(c, line_count=10, title="서술형 답안지")
    c.save()

    buf.seek(0)
    return buf.read()
