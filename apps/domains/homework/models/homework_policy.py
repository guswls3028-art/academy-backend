# PATH: apps/domains/homework/models/homework_policy.py

from __future__ import annotations

from django.db import models

from apps.api.common.models import TimestampModel
from apps.domains.lectures.models import Session
from apps.core.models import Tenant


class HomeworkPolicy(TimestampModel):
    """
    Session 단위 과제 판정 정책
    """

    tenant = models.ForeignKey(
        Tenant,
        on_delete=models.CASCADE,
        related_name="homework_policies",
        db_index=True,  # ✅ tenant_id 인덱스 추가
    )

    session = models.OneToOneField(
        Session,
        on_delete=models.CASCADE,
        related_name="homework_policy",
    )

    cutline_percent = models.PositiveSmallIntegerField(default=80)
    round_unit_percent = models.PositiveSmallIntegerField(default=5)

    # ✅ 과제 커트라인: 퍼센트(%) 또는 문항 수(COUNT) 기준
    class CutlineMode(models.TextChoices):
        PERCENT = "PERCENT", "퍼센트 (%)"
        COUNT = "COUNT", "문항 수"

    cutline_mode = models.CharField(
        max_length=10,
        choices=CutlineMode.choices,
        default=CutlineMode.PERCENT,
    )
    cutline_value = models.PositiveSmallIntegerField(
        default=80,
        help_text="PERCENT: 0-100 퍼센트, COUNT: 최소 정답 문항 수(점수)",
    )

    clinic_enabled = models.BooleanField(default=True)
    clinic_on_fail = models.BooleanField(default=True)

    class Meta:
        ordering = ["-updated_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "session"],
                name="uniq_homework_policy_per_tenant_session",
            )
        ]

    def __str__(self):
        return (
            f"HomeworkPolicy("
            f"tenant={self.tenant_id}, "
            f"session={self.session_id})"
        )
