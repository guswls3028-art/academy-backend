# apps/domains/results/models/wrong_note_pdf.py
from django.db import models
from apps.api.common.models import BaseModel


class WrongNotePDF(BaseModel):
    """
    오답노트 PDF 생성 Job
    """

    class Status(models.TextChoices):
        PENDING = "PENDING"
        RUNNING = "RUNNING"
        DONE = "DONE"
        FAILED = "FAILED"

    class OutputFormat(models.TextChoices):
        PDF = "pdf", "PDF"
        HWPX = "hwpx", "한글(HWPX)"

    enrollment = models.ForeignKey(
        "enrollment.Enrollment",
        on_delete=models.CASCADE,
        db_column="enrollment_id",
        related_name="wrong_note_pdf_jobs",
    )
    lecture = models.ForeignKey(
        "lectures.Lecture",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        db_column="lecture_id",
        related_name="wrong_note_pdf_jobs",
    )
    exam = models.ForeignKey(
        "exams.Exam",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        db_column="exam_id",
        related_name="wrong_note_pdf_jobs",
    )

    from_session_order = models.PositiveIntegerField(default=2)
    to_session_order = models.PositiveIntegerField(null=True, blank=True)

    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
    )
    output_format = models.CharField(
        max_length=8,
        choices=OutputFormat.choices,
        default=OutputFormat.PDF,
    )

    file_path = models.CharField(max_length=255, blank=True)
    error_message = models.TextField(blank=True)

    class Meta:
        db_table = "results_wrong_note_pdf"
        ordering = ["-created_at"]
