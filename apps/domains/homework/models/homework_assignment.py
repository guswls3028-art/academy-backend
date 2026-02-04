# PATH: apps/domains/homework/models/homework_assignment.py

from __future__ import annotations

from django.db import models

from apps.domains.homework_results.models import Homework
from apps.domains.lectures.models import Session
from apps.core.models import Tenant


class HomeworkAssignment(models.Model):
    """
    HomeworkAssignment (과제별 대상자)
    """

    tenant = models.ForeignKey(
        Tenant,
        on_delete=models.CASCADE,
        related_name="homework_assignments",
    )

    homework = models.ForeignKey(
        Homework,
        on_delete=models.CASCADE,
        related_name="assignments",
    )

    session = models.ForeignKey(
        Session,
        on_delete=models.CASCADE,
        db_index=True,
    )

    enrollment_id = models.IntegerField(db_index=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "homework_assignment"
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "homework", "enrollment_id"],
                name="uniq_homework_assignment_per_tenant",
            )
        ]

    def __str__(self) -> str:
        return (
            f"HomeworkAssignment("
            f"tenant={self.tenant_id}, "
            f"homework={self.homework_id}, "
            f"enrollment={self.enrollment_id})"
        )
