from django.db import models

from apps.domains.students.models import Student
from apps.domains.lectures.models import Lecture
from .category import MaterialCategory


class Material(models.Model):
    lecture = models.ForeignKey(
        Lecture,
        on_delete=models.CASCADE,
        related_name="materials",
    )
    category = models.ForeignKey(
        MaterialCategory,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="materials",
    )

    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)

    file = models.FileField(upload_to="materials/%Y/%m/%d/", blank=True)
    url = models.URLField(blank=True)

    order = models.PositiveIntegerField(default=1)
    is_public = models.BooleanField(default=True)

    uploaded_by = models.ForeignKey(
        Student,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["category", "order", "-created_at"]

    def __str__(self):
        return self.title
