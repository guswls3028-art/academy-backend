from django.db import models

from apps.api.common.models import TimestampModel
from apps.domains.students.models import Student
from apps.domains.lectures.models import Lecture, Session


# ========================================================
# Enrollment (강의 단위 수강 등록)
# ========================================================

class Enrollment(TimestampModel):
    """
    학생이 특정 강의를 수강하는 행위.
    강의 정의(Lecture)와 분리된 '수강 행위' 도메인이다.
    """

    student = models.ForeignKey(
        Student,
        on_delete=models.CASCADE,
        related_name="enrollments",
    )
    lecture = models.ForeignKey(
        Lecture,
        on_delete=models.CASCADE,
        related_name="enrollments",
    )

    status = models.CharField(
        max_length=20,
        choices=[
            ("ACTIVE", "활성"),
            ("INACTIVE", "비활성"),
            ("PENDING", "대기"),
        ],
        default="ACTIVE",
    )

    enrolled_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["student", "lecture"],
                name="unique_enrollment_per_lecture",
            )
        ]

    def __str__(self):
        return f"{self.student.name} -> {self.lecture.title}"


# ========================================================
# SessionEnrollment (차시 단위 수강 권한)
# ========================================================

class SessionEnrollment(models.Model):
    """
    특정 Enrollment가 어떤 Session(차시)에 접근 가능한지 정의.
    출결/영상/자료 접근의 기준이 되는 중간 테이블.
    """

    session = models.ForeignKey(
        Session,
        on_delete=models.CASCADE,
        related_name="session_enrollments",
    )
    enrollment = models.ForeignKey(
        Enrollment,
        on_delete=models.CASCADE,
        related_name="session_enrollments",
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("session", "enrollment")

    def __str__(self):
        return f"{self.session} - {self.enrollment.student.name}"
