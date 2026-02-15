from django.db import models
from apps.domains.lectures.models import Lecture


# ========================================================
# D-Day
# ========================================================

class Dday(models.Model):
    """
    강의 단위 주요 일정 (시험, 마감, 이벤트 등)
    """

    lecture = models.ForeignKey(
        Lecture,
        on_delete=models.CASCADE,
        related_name="ddays",
        db_index=True,
    )

    title = models.CharField(max_length=255)

    # 날짜 + 시간까지 필요하므로 DateTimeField 유지
    date = models.DateTimeField()

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["date"]
        verbose_name = "D-Day"
        verbose_name_plural = "D-Days"

    def __str__(self):
        return f"[{self.lecture.title}] {self.title} ({self.date})"
