# PATH: apps/domains/homework/models/homework_enrollment.py

from __future__ import annotations

from django.db import models
from apps.core.models import Tenant


class HomeworkEnrollment(models.Model):
    """
    Session 단위 과제 응시 등록
    """

    tenant = models.ForeignKey(
        Tenant,
        on_delete=models.CASCADE,
        related_name="homework_enrollments",
    )

    session = models.ForeignKey(
        "lectures.Session",
        on_delete=models.CASCADE,
        db_column="session_id",
        related_name="homework_enrollments",
    )
    enrollment = models.ForeignKey(
        "enrollment.Enrollment",
        on_delete=models.CASCADE,
        db_column="enrollment_id",
        related_name="homework_enrollments",
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "homework_enrollment"
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "session", "enrollment"],
                name="uniq_homework_enrollment_per_tenant",
            )
        ]

    def __str__(self) -> str:
        return (
            f"HomeworkEnrollment("
            f"tenant={self.tenant_id}, "
            f"session={self.session_id}, "
            f"enrollment={self.enrollment_id})"
        )
