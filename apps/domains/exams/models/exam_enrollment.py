# PATH: apps/domains/exams/models/exam_enrollment.py

from __future__ import annotations

from django.db import models


class ExamEnrollment(models.Model):
    """
    ExamEnrollment
    - 시험(Exam) 응시 대상자 엔티티
    - 세션 등록(SessionEnrollment)의 부분집합만 가능
    - "시험마다 응시생이 달라질 수 있음"을 지원하기 위한 구조

    ✅ 단일 진실:
    - 수강생/세션 등록: SessionEnrollment
    - 시험 응시 대상자: ExamEnrollment
    """

    exam = models.ForeignKey(
        "exams.Exam",
        on_delete=models.CASCADE,
        related_name="exam_enrollments",
    )

    enrollment = models.ForeignKey(
        "enrollment.Enrollment",
        on_delete=models.CASCADE,
        related_name="exam_enrollments",
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "exams_exam_enrollment"
        unique_together = [("exam", "enrollment")]

    def __str__(self) -> str:
        return f"ExamEnrollment(exam={self.exam_id}, enrollment={self.enrollment_id})"
