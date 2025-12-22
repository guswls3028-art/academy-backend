from django.db import models
from apps.api.common.models import TimestampModel


class Parent(TimestampModel):
    name = models.CharField(max_length=50)
    phone = models.CharField(max_length=20)

    email = models.EmailField(null=True, blank=True)
    memo = models.TextField(null=True, blank=True)

    class Meta:
        ordering = ["-id"]

    def __str__(self):
        return f"{self.name} ({self.phone})"
