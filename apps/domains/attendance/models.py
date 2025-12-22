from django.db import models


# ========================================================
# Attendance
# ========================================================

class Attendance(models.Model):
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
        constraints = [
            models.UniqueConstraint(
                fields=["enrollment", "session"],
                name="unique_attendance_per_session",
            )
        ]

    def __str__(self):
        return f"{self.enrollment.student.name} / {self.session.title} / {self.status}"
