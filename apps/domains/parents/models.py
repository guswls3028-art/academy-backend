# PATH: apps/domains/parents/models.py
from django.db import models
from apps.api.common.models import TimestampModel
from apps.core.models import Tenant
from apps.core.db import TenantQuerySet


class Parent(TimestampModel):
    # 🔐 tenant-safe manager
    objects = TenantQuerySet.as_manager()

    tenant = models.ForeignKey(
        Tenant,
        on_delete=models.CASCADE,
        related_name="parents",
    )

    name = models.CharField(max_length=50)
    phone = models.CharField(max_length=20)

    email = models.EmailField(null=True, blank=True)
    memo = models.TextField(null=True, blank=True)

    class Meta:
        ordering = ["-id"]

    def __str__(self):
        return f"{self.name} ({self.phone})"
