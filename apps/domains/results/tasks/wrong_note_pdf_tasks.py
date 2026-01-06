# apps/domains/results/tasks/wrong_note_pdf_tasks.py
from __future__ import annotations

from io import BytesIO
from typing import Optional

from celery import shared_task
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage

from apps.domains.results.models import WrongNotePDF
from apps.domains.results.services.wrong_note_service import (
    WrongNoteQuery,
    list_wrong_notes_for_enrollment,
)


# ======================================================
# ✅ STEP 1: reportlab 지연 import (정석)
# - API 서버 / migrate / runserver 환경에서 reportlab이 없어도 OK
# - PDF worker 환경에서만 reportlab 필요
# ======================================================
def _import_reportlab():
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.pdfgen import canvas
        return A4, canvas
    except ImportError as e:
        raise RuntimeError(
            "reportlab is required only on PDF worker environment"
        ) from e


@shared_task(bind=True, max_retries=3, default_retry_delay=10)
def generate_wrong_note_pdf_task(self, job_id: int) -> None:
    """
    오답노트 PDF 생성 Worker (Celery)

    ✅ STEP 3-3 반영:
    - lecture_id/from_session_order 필터까지 service로 통일

    ✅ STEP 1 (중요):
    - reportlab 지연 import 적용
      -> 이 task가 실제 실행될 때만 reportlab import
    """

    job = WrongNotePDF.objects.filter(id=int(job_id)).first()
    if not job:
        return

    # 멱등성/중복 실행 방지
    if job.status == WrongNotePDF.Status.DONE:
        return
    if job.status == WrongNotePDF.Status.RUNNING:
        return

    def _set_status(status: str, error: str = "") -> None:
        job.status = status
        job.error_message = (error or "")[:2000]
        job.save(update_fields=["status", "error_message"])

    try:
        _set_status(WrongNotePDF.Status.RUNNING)

        enrollment_id = int(job.enrollment_id)

        q = WrongNoteQuery(
            exam_id=int(job.exam_id) if job.exam_id else None,
            lecture_id=int(job.lecture_id) if job.lecture_id else None,
            from_session_order=int(job.from_session_order or 2),
            offset=0,
            limit=200,  # PDF는 우선 상위 200개
        )

        total, items = list_wrong_notes_for_enrollment(
            enrollment_id=enrollment_id,
            q=q,
        )

        # --------------------------------------------------
        # ✅ STEP 1: reportlab은 "실제 PDF 생성 시점"에만 import
        # --------------------------------------------------
        A4, canvas = _import_reportlab()

        # ------------------------------
        # PDF 생성 (최소 구현: 텍스트 리스트)
        # ------------------------------
        buf = BytesIO()
        c = canvas.Canvas(buf, pagesize=A4)
        _, height = A4

        y = height - 48
        c.setFont("Helvetica-Bold", 14)
        c.drawString(40, y, "Wrong Notes")
        y -= 20

        c.setFont("Helvetica", 10)
        c.drawString(40, y, f"Enrollment: {enrollment_id}")
        y -= 14
        c.drawString(40, y, f"Total wrong items: {total}")
        y -= 20

        for idx, it in enumerate(items, start=1):
            if y < 80:
                c.showPage()
                y = height - 60
                c.setFont("Helvetica", 10)

            line = (
                f"{idx}. "
                f"Exam {it.get('exam_id')} / "
                f"Q{it.get('question_number') or it.get('question_id')} "
                f"| ans={it.get('student_answer','')} "
                f"| correct={it.get('correct_answer','')} "
                f"| score={it.get('score',0)}/{it.get('max_score',0)}"
            )
            c.drawString(40, y, line[:120])
            y -= 14

        c.showPage()
        c.save()
        buf.seek(0)

        key = f"results/wrong_notes/{int(job.id)}.pdf"
        default_storage.save(key, ContentFile(buf.read()))

        job.file_path = key
        job.status = WrongNotePDF.Status.DONE
        job.error_message = ""
        job.save(update_fields=["file_path", "status", "error_message"])

    except Exception as e:
        msg = str(e)
        try:
            _set_status(WrongNotePDF.Status.FAILED, msg)
        finally:
            # ✅ 기존 동작 존중: retry로 재시도
            raise self.retry(exc=e)
