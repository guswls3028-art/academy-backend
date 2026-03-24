# apps/domains/exams/models/question_explanation.py
"""
문항별 해설 모델.

설계 원칙:
- ExamQuestion과 1:1 관계 (한 문항에 하나의 해설)
- text: OCR/AI 추출 또는 강사 직접 입력 해설 텍스트
- image_key: R2에 저장된 해설 이미지 키 (PDF 해설 영역 크롭)
- source: 해설의 출처 (ai_extracted, manual)
- AI 추출 결과를 강사가 수정할 수 있도록 설계
"""
from django.db import models
from apps.core.models.base import BaseModel


class QuestionExplanation(BaseModel):
    """문항별 해설."""

    class Source(models.TextChoices):
        AI_EXTRACTED = "ai_extracted", "AI 추출"
        MANUAL = "manual", "수동 입력"

    question = models.OneToOneField(
        "exams.ExamQuestion",
        on_delete=models.CASCADE,
        related_name="explanation",
    )

    text = models.TextField(
        blank=True,
        default="",
        help_text="해설 텍스트 (AI 추출 또는 강사 입력)",
    )

    image_key = models.CharField(
        max_length=500,
        blank=True,
        default="",
        help_text="R2에 저장된 해설 이미지 키",
    )

    source = models.CharField(
        max_length=20,
        choices=Source.choices,
        default=Source.MANUAL,
    )

    # AI 추출 시 매칭 신뢰도 (0.0 ~ 1.0)
    match_confidence = models.FloatField(
        null=True,
        blank=True,
        help_text="AI 문항-해설 매칭 신뢰도",
    )

    class Meta:
        db_table = "exams_question_explanation"
        verbose_name = "문항 해설"

    def __str__(self):
        return f"Explanation for {self.question}"
