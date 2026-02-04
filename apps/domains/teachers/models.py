# PATH: apps/domains/teachers/models.py
from django.db import models
from apps.api.common.models import TimestampModel
from apps.core.models import Tenant
from apps.core.db import TenantQuerySet


class Teacher(TimestampModel):
    # 🔐 tenant-safe manager
    objects = TenantQuerySet.as_manager()

    tenant = models.ForeignKey(
        Tenant,
        on_delete=models.CASCADE,
        related_name="teachers",
    )

    name = models.CharField(max_length=50)
    phone = models.CharField(max_length=20, null=True, blank=True)
    email = models.EmailField(null=True, blank=True)

    subject = models.CharField(max_length=50, null=True, blank=True)
    note = models.TextField(null=True, blank=True)

    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["-id"]

    def __str__(self):
        return self.name
