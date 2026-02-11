# PATH: apps/core/models/tenant.py
from django.db import models


class Tenant(models.Model):
    """
    Tenant == Academy
    SaaS 단위의 학원
    """

    name = models.CharField(max_length=255)
    code = models.CharField(max_length=50, unique=True)

    owner_name = models.CharField(max_length=100, blank=True)
    phone = models.CharField(max_length=50, blank=True)
    address = models.CharField(max_length=255, blank=True)

    logo = models.ImageField(
        upload_to="academy/logo/",
        null=True,
        blank=True,
    )

    is_active = models.BooleanField(default=True)

    class Meta:
        app_label = "core"
        verbose_name = "Tenant"
        verbose_name_plural = "Tenants"

    def __str__(self):
        return self.name
