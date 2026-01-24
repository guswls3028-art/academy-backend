# PATH: apps/domains/homework/models/homework_enrollment.py
from __future__ import annotations

from django.db import models


class HomeworkEnrollment(models.Model):
    """
    HomeworkEnrollment (Session 1:1 과제 응시 등록)

    ✅ 단일 진실:
    - "해당 세션에서 과제를 수행해야 하는 학생" 등록 엔티티
    - HomeworkScore(점수 스냅샷)와 별개
      -> 등록만 되어도 성적탭 rows에 포함되게 만들기 위함

    Key:
    - session_id + enrollment_id unique
    """

    session_id = models.IntegerField(db_index=True)
    enrollment_id = models.IntegerField(db_index=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "homework_enrollment"
        constraints = [
            models.UniqueConstraint(
                fields=["session_id", "enrollment_id"],
                name="uniq_homework_enrollment_session_enrollment",
            )
        ]

    def __str__(self) -> str:
        return f"HomeworkEnrollment(session={self.session_id}, enrollment={self.enrollment_id})"
