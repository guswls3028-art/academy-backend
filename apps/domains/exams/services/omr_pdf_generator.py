# PATH: apps/domains/exams/services/omr_pdf_generator.py
from __future__ import annotations

import io
from typing import Optional

from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm


def generate_simple_objective_omr_pdf(*, title: str, question_count: int) -> bytes:
    """
    Objective OMR PDF (간단 생성 버전)

    ⚠️ 주의:
    - 이 버전은 "인쇄 가능한 OMR"을 최소 기능으로 생성한다.
    - 네 프로젝트의 좌표 SSOT를 assets/meta로 완전히 맞추려면,
      다음 단계에서 assets meta 기반 렌더링으로 업그레이드하면 된다.
    """
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    w, h = A4

    # header
    c.setFont("Helvetica-Bold", 14)
    c.drawString(20 * mm, h - 20 * mm, f"OMR Answer Sheet")
    c.setFont("Helvetica", 11)
    c.drawString(20 * mm, h - 28 * mm, f"Title: {title}")
    c.drawString(20 * mm, h - 35 * mm, f"Questions: {question_count}")

    # layout
    # 10/20/30을 2~3열로 분산
    cols = 2 if question_count <= 20 else 3
    per_col = (question_count + cols - 1) // cols

    start_x = 20 * mm
    start_y = h - 50 * mm
    col_w = 60 * mm
    row_h = 7 * mm

    bubble_r = 2.2 * mm
    choices = ["A", "B", "C", "D", "E"]
    choice_gap = 8 * mm

    c.setFont("Helvetica", 9)

    qn = 1
    for col in range(cols):
        x0 = start_x + col * col_w
        y = start_y
        for _ in range(per_col):
            if qn > question_count:
                break

            # number
            c.drawString(x0, y, f"{qn:02d}")

            # bubbles
            bx = x0 + 12 * mm
            for ch in choices:
                c.circle(bx, y + 2.2 * mm, bubble_r, stroke=1, fill=0)
                c.drawString(bx - 1.5 * mm, y - 2.2 * mm, ch)
                bx += choice_gap

            y -= row_h
            qn += 1

    c.showPage()
    c.save()
    return buf.getvalue()
