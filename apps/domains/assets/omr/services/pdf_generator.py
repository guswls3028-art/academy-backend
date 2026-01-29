# apps/domains/assets/omr/services/pdf_generator.py
from __future__ import annotations

from io import BytesIO
from typing import Optional

from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader

from apps.domains.assets.omr import constants as C
from apps.domains.assets.omr.layouts import objective_v1_10, objective_v1_20, objective_v1_30


class LogoValidationError(Exception):
    pass


def _build_logo_reader(logo_file) -> Optional[ImageReader]:
    """
    - reportlab ImageReader로 실제 파싱을 시도해서 "진짜 이미지"만 통과시킨다.
    - 변환 라이브러리 추가 없이 reportlab 기본으로 처리.
    """
    if logo_file is None:
        return None

    # DRF InMemoryUploadedFile / TemporaryUploadedFile 모두 file-like
    try:
        logo_file.seek(0)
    except Exception:
        pass

    try:
        reader = ImageReader(logo_file)
        # ImageReader가 내부 파싱을 미루는 경우가 있어 size 접근으로 한 번 더 검증
        _ = reader.getSize()
        return reader
    except Exception as e:
        raise LogoValidationError("logo must be a valid image file") from e


def generate_objective_pdf(*, question_count: int, logo_file=None) -> bytes:
    """
    Stateless PDF generator (시험지 1: 객관식 전용 OMR)
    - A4 landscape
    - 3단 레이아웃
    - question_count: 10/20/30 only
    """
    if question_count not in C.ALLOWED_QUESTION_COUNTS:
        raise ValueError("invalid question_count")

    logo_reader = _build_logo_reader(logo_file)

    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=C.PAGE_SIZE)

    # layout dispatch (no branching inside layout files)
    if question_count == 10:
        objective_v1_10.draw(c, logo_reader=logo_reader)
    elif question_count == 20:
        objective_v1_20.draw(c, logo_reader=logo_reader)
    else:
        objective_v1_30.draw(c, logo_reader=logo_reader)

    c.save()
    buf.seek(0)
    return buf.read()
