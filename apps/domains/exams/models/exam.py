# apps/domains/exams/models/exam.py
from django.db import models
from apps.api.common.models import BaseModel


class Exam(BaseModel):
    """
    시험 정의 (메타 정보 + 운영 정책)

    ✅ 도메인 책임(단일 진실):
    - exams: 시험 메타 + 배포용 자산(문제PDF/OMR) + 재시험 정책(allow/max/pass/open/close)
    - submissions: 제출 이벤트 단일 진실
    - results: attempt/채점/대표 attempt/조회 API

    ⚠️ 주의:
    - allow_retake=False면 max_attempts는 사실상 1로 취급됨
      (정책 강제는 results.ExamAttemptService가 수행)
    """

    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    subject = models.CharField(max_length=100)

    # 예: 중간고사, 모의고사, 클리닉 테스트 등
    exam_type = models.CharField(
        max_length=50,
        default="regular",
    )

    is_active = models.BooleanField(default=True)

    # =====================================================
    # ✅ STEP 1/3: 재시험 정책
    # =====================================================
    allow_retake = models.BooleanField(default=False)
    max_attempts = models.PositiveIntegerField(default=1)  # allow_retake=False면 1로 취급
    pass_score = models.FloatField(default=0.0)

    # =====================================================
    # ✅ 시험 공개/마감
    # =====================================================
    open_at = models.DateTimeField(null=True, blank=True)
    close_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "exams_exam"
        ordering = ["-created_at"]

    def __str__(self):
        return self.title
