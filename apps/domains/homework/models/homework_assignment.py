from __future__ import annotations

from django.db import models

from apps.domains.homework_results.models import Homework
from apps.domains.lectures.models import Session


class HomeworkAssignment(models.Model):
    """
    HomeworkAssignment (과제별 대상자)

    ✅ 단일 진실:
    - 특정 Homework(과제)에 대해
      어떤 enrollment_id가 대상자인지 관리

    기존 HomeworkEnrollment(session 단위)는 유지하되
    개별 과제에서는 이 테이블을 우선 사용한다.
    """

    homework = models.ForeignKey(
        Homework,
        on_delete=models.CASCADE,
        related_name="assignments",
    )

    # 성능/필터 편의용 (중복 저장 허용)
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
                fields=["homework", "enrollment_id"],
                name="uniq_homework_assignment_homework_enrollment",
            )
        ]

    def __str__(self) -> str:
        return (
            f"HomeworkAssignment("
            f"homework={self.homework_id}, "
            f"enrollment={self.enrollment_id})"
        )
