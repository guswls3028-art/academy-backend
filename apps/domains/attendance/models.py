# PATH: apps/domains/attendance/models.py

from django.db import models
from apps.core.models import Tenant


# ========================================================
# Attendance
# ========================================================

class Attendance(models.Model):
    tenant = models.ForeignKey(
        Tenant,
        on_delete=models.CASCADE,
        related_name="lecture_attendances",  # ✅ 변경 (충돌 방지)
        null=False,  # ✅ NOT NULL로 변경 (프로덕션 준비)
        blank=False,
        db_index=True,  # ✅ tenant_id 인덱스 추가
    )

    enrollment = models.ForeignKey(
        "enrollment.Enrollment",
        on_delete=models.CASCADE,
        related_name="attendances",
    )
    session = models.ForeignKey(
        "lectures.Session",
        on_delete=models.CASCADE,
        related_name="attendances",
    )

    status = models.CharField(
        max_length=20,
        choices=[
            ("PRESENT", "출석"),
            ("LATE", "지각"),
            ("ONLINE", "온라인"),
            ("SUPPLEMENT", "보강"),
            ("EARLY_LEAVE", "조퇴"),
            ("ABSENT", "결석"),
            ("RUNAWAY", "출튀"),
            ("MATERIAL", "자료"),
            ("INACTIVE", "부재"),
            ("SECESSION", "탈퇴"),
        ],
        default="PRESENT",
    )

    memo = models.TextField(blank=True)
    recorded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["tenant", "recorded_at"]),  # ✅ 복합 인덱스 추가
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "enrollment", "session"],
                name="unique_attendance_per_tenant_session",
            )
        ]

    def __str__(self):
        return f"{self.enrollment.student.name} / {self.session.title} / {self.status}"
