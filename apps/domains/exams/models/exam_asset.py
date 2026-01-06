# apps/domains/exams/models/exam_asset.py
from __future__ import annotations

from django.db import models
from apps.api.common.models import BaseModel


class ExamAsset(BaseModel):
    """
    시험 배포용 파일 자산 (R2 기반)

    ✅ 책임:
    - 문제 PDF / OMR 답안지 등 "다운로드 가능한 파일"만 관리
    - 업로드/다운로드 URL은 serializer에서 presigned GET으로 제공

    ⚠️ 운영 규칙:
    - exam + asset_type는 1개만 유지(update_or_create)
      → teacher가 최신 파일로 교체해도 식별은 동일
    """

    class AssetType(models.TextChoices):
        PROBLEM_PDF = "problem_pdf", "Problem PDF"
        OMR_SHEET = "omr_sheet", "OMR Sheet"

    exam = models.ForeignKey(
        "exams.Exam",
        on_delete=models.CASCADE,
        related_name="assets",
    )

    asset_type = models.CharField(
        max_length=30,
        choices=AssetType.choices,
    )

    # ✅ R2
    file_key = models.CharField(max_length=512)
    file_type = models.CharField(max_length=50, null=True, blank=True)
    file_size = models.PositiveIntegerField(null=True, blank=True)

    class Meta:
        db_table = "exams_exam_asset"
        unique_together = ("exam", "asset_type")
        indexes = [
            models.Index(fields=["exam", "asset_type"]),
        ]

    def __str__(self):
        return f"{self.exam_id}:{self.asset_type}"
