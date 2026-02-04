# PATH: apps/domains/lectures/models.py

from django.db import models
from apps.api.common.models import TimestampModel
from apps.core.models import Tenant
from apps.core.db import TenantQuerySet  # ✅ 추가


class Lecture(TimestampModel):
    """
    강의 (Course / Lecture)

    - 학원(Tenant) 단위로 완전 분리
    - 여러 Session(차시)을 가진다
    """

    # 🔐 tenant-safe manager
    objects = TenantQuerySet.as_manager()

    tenant = models.ForeignKey(
        Tenant,
        on_delete=models.CASCADE,
        related_name="lectures",
    )

    title = models.CharField(max_length=255)
    name = models.CharField(max_length=255)
    subject = models.CharField(max_length=50)
    description = models.TextField(blank=True)

    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)

    is_active = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "title"],
                name="uniq_lecture_title_per_tenant",
            )
        ]

    def __str__(self):
        return self.title


class Session(TimestampModel):
    lecture = models.ForeignKey(
        Lecture,
        on_delete=models.CASCADE,
        related_name="sessions",
    )

    order = models.PositiveIntegerField()
    title = models.CharField(max_length=255)
    date = models.DateField(null=True, blank=True)

    class Meta:
        ordering = ["order"]

    def __str__(self):
        return f"{self.lecture.title} - {self.order}차시"
