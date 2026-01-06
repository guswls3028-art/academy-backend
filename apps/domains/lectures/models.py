# PATH: apps/domains/lectures/models.py

from django.db import models
from apps.api.common.models import TimestampModel


# ========================================================
# Lecture
# ========================================================

class Lecture(TimestampModel):
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
    lecture = models.ForeignKey(
        Lecture,
        on_delete=models.CASCADE,
        related_name="sessions",
    )

    # ==========================================================
    # ✅ SaaS 표준: Session ↔ Exam FK
    #
    # - Session(차시) = 실행 단위
    # - Exam(시험)   = 정의 단위
    #
    # 정책:
    # - 시험이 없는 차시 허용 (null=True)
    # - Exam 삭제 시 Session 유지 (SET_NULL)
    # - Exam → sessions 역참조 가능
    # ==========================================================
    exam = models.ForeignKey(
        "exams.Exam",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="sessions",
    )

    order = models.PositiveIntegerField()
    title = models.CharField(max_length=255)
    date = models.DateField(null=True, blank=True)

    class Meta:
        ordering = ["order"]

    def __str__(self):
        return f"{self.lecture.title} - {self.order}차시"
