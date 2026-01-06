# apps/domains/exams/models/question.py
from django.db import models
from apps.api.common.models import BaseModel
from .sheet import Sheet


class ExamQuestion(BaseModel):
    """
    시험 문항 정의

    설계 원칙:
    - 문항의 '의미'는 number로만 식별
    - 채점/정답 여부/점수 계산 ❌ (results 도메인 책임)
    - 이 모델은 "시험지 위의 문항 위치 + 점수 단위"만 관리

    region_meta:
    - OMR / Vision Worker가 제공한 문항 영역 정보
    - 예: {"x": 12, "y": 34, "w": 120, "h": 45}
    - 재채점 / 오답노트 / 문항 하이라이트에 필수
    """

    sheet = models.ForeignKey(
        Sheet,
        on_delete=models.CASCADE,
        related_name="questions",
    )

    number = models.PositiveIntegerField()  # 1번, 2번 ...
    score = models.FloatField(default=1.0)

    # 문항 이미지 (AI로 잘라낸 결과 포함 가능)
    image = models.ImageField(
        upload_to="exams/questions/",
        null=True,
        blank=True,
    )

    # 🔥 STEP 2 필수: 문항 영역 메타 (bbox)
    # worker segmentation 결과를 그대로 저장
    # 형식 예: {"x": 10, "y": 20, "w": 100, "h": 40}
    region_meta = models.JSONField(
        null=True,
        blank=True,
    )

    class Meta:
        db_table = "exams_question"
        unique_together = ("sheet", "number")
        ordering = ["number"]

    def __str__(self):
        return f"{self.sheet} Q{self.number}"
