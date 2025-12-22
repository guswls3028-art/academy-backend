from django.db import models


# ========================================================
# Counseling
# ========================================================

class Counseling(models.Model):
    """
    수강 중 발생하는 1:1 상담 기록.
    Lecture가 아닌 Enrollment 컨텍스트에 종속된다.
    """

    enrollment = models.ForeignKey(
        "enrollment.Enrollment",
        on_delete=models.CASCADE,
        related_name="counselings",
    )

    content = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.enrollment.student.name} 상담"
