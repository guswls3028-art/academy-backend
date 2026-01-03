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

    enrollment_id = models.PositiveIntegerField()
    lecture_id = models.PositiveIntegerField(null=True, blank=True)
    exam_id = models.PositiveIntegerField(null=True, blank=True)

    from_session_order = models.PositiveIntegerField(default=2)

    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
    )

    file_path = models.CharField(max_length=255, blank=True)
    error_message = models.TextField(blank=True)

    class Meta:
        db_table = "results_wrong_note_pdf"
        ordering = ["-created_at"]
