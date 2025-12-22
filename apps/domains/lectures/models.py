from django.db import models

from apps.api.common.models import TimestampModel


# ========================================================
# Lecture
# ========================================================

class Lecture(TimestampModel):
    title = models.CharField(max_length=255)
    name = models.CharField(max_length=255)
    subject = models.CharField(max_length=50)
    description = models.TextField(blank=True)

    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)

    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.title


# ========================================================
# Session
# ========================================================

class Session(TimestampModel):
    lecture = models.ForeignKey(
        Lecture,
        on_delete=models.CASCADE,
        related_name="sessions",
    )

    order = models.PositiveIntegerField()
    title = models.CharField(max_length=255)
    date = models.DateField(null=True, blank=True)

    class Meta:
        ordering = ["order"]

    def __str__(self):
        return f"{self.lecture.title} - {self.order}차시"
