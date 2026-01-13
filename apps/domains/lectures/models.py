# PATH: apps/domains/lectures/models.py

from django.db import models
from apps.api.common.models import TimestampModel


# ========================================================
# Lecture
# ========================================================

class Lecture(TimestampModel):
    """
    강의 (Course / Lecture)

    - 여러 Session(차시)을 가진다
    - 시험과 직접 연결되지 않는다
      (시험은 Session 단위로 운영됨)
    """

    title = models.CharField(max_length=255)
    name = models.CharField(max_length=255)
    subject = models.CharField(max_length=50)
    description = models.TextField(blank=True)

    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)

    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.title


# ========================================================
# Session
# ========================================================

class Session(TimestampModel):
    """
    차시 (Session)

    🔥 핵심 설계 결정 (중요):

    ❌ Session.exam (ForeignKey) 제거
    ✅ Exam.sessions (ManyToManyField) 를 단일 진실로 사용

    이유:
    - Session : Exam = 1:N / N:M 구조 공식 지원
    - "차시에 시험이 1개"라는 암묵적 가정 제거
    - 성적/Progress/통계 로직의 안정성 확보
    - Django reverse accessor 충돌(E302/E303) 해결

    연결 방식:
    - Session → Exam:
        session.exams.all()        (reverse M2M)
    - Exam → Session:
        exam.sessions.all()        (정방향 M2M)

    ⚠️ 주의:
    - "이 차시에 시험이 있었는가?"
        → SessionProgress.exam_attempted 로 판단
    - "시험 결과 / 합불 / 점수"
        → Result + Progress 집계 책임
    """

    lecture = models.ForeignKey(
        Lecture,
        on_delete=models.CASCADE,
        related_name="sessions",
    )

    # 차시 순서 (1차시, 2차시 ...)
    order = models.PositiveIntegerField()

    # 차시 제목
    title = models.CharField(max_length=255)

    # 차시 날짜 (선택)
    date = models.DateField(null=True, blank=True)

    class Meta:
        ordering = ["order"]

    def __str__(self):
        return f"{self.lecture.title} - {self.order}차시"
