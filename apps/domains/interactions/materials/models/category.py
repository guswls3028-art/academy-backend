from django.db import models
from apps.domains.lectures.models import Lecture


class MaterialCategory(models.Model):
    lecture = models.ForeignKey(
        Lecture,
        on_delete=models.CASCADE,
        related_name="material_categories",
    )
    name = models.CharField(max_length=100)
    order = models.PositiveIntegerField(default=1)

    class Meta:
        ordering = ["order", "id"]

    def __str__(self):
        return f"[{self.lecture.title}] {self.name}"
