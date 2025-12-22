from django.db import models

from apps.domains.students.models import Student
from apps.domains.lectures.models import Session
from apps.domains.enrollment.models import Enrollment
from .material import Material


class MaterialAccess(models.Model):
    """
    특정 Material에 대해 '누가 / 언제 / 어떤 범위로' 접근 가능한지 정의
    """

    material = models.ForeignKey(
        Material,
        on_delete=models.CASCADE,
        related_name="accesses",
    )

    student = models.ForeignKey(
        Student,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="material_accesses",
    )
    enrollment = models.ForeignKey(
        Enrollment,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="material_accesses",
    )
    session = models.ForeignKey(
        Session,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="material_accesses",
    )

    available_from = models.DateTimeField(null=True, blank=True)
    available_until = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["material"]),
            models.Index(fields=["student"]),
            models.Index(fields=["enrollment"]),
            models.Index(fields=["session"]),
        ]

    def __str__(self):
        return f"Access: {self.material.title}"
