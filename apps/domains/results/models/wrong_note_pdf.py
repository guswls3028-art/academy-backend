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
        db_default=OutputFormat.PDF,
    )
    source_fingerprint = models.CharField(
        max_length=64,
        blank=True,
        db_default="",
        help_text="생성 요청 시점의 오답·문항·해설 내용 SHA-256",
    )
    source_selection = models.JSONField(
        default=list,
        blank=True,
        help_text=(
            "학생의 여러 강의에서 선택한 시험·워크북 원본 목록. "
            "비어 있으면 기존 단일 수강·회차 범위 규칙을 사용한다."
        ),
    )

    file_path = models.CharField(max_length=255, blank=True)
    error_message = models.TextField(blank=True)

    class Meta:
        db_table = "results_wrong_note_pdf"
        ordering = ["-created_at"]
