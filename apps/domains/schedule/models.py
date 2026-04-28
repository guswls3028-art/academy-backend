from django.db import models
from apps.domains.lectures.models import Lecture


# ========================================================
# D-Day  ⚠️ DEPRECATED
# ========================================================
# 이 도메인 전체가 폐기 대상이다. urls/views/serializers/admin/frontend 사용처 0건.
# 모델·테이블은 데이터 안전을 위해 잔존. 추후 마이그레이션으로 drop + INSTALLED_APPS 제거 예정.
# 신규 코드는 절대 이 모델을 import 하지 말 것.

class Dday(models.Model):
    """
    [DEPRECATED] 강의 단위 주요 일정 (시험, 마감, 이벤트 등)
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
