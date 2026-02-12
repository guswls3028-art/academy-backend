# PATH: apps/domains/parents/models.py
from django.conf import settings
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
        db_index=True,  # ✅ tenant_id 인덱스 추가
    )

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="parent_profile",
        help_text="학부모 로그인 계정 (ID = 학부모 전화번호)",
    )

    name = models.CharField(max_length=50)
    phone = models.CharField(
        max_length=20,
        help_text="정규화된 전화번호 (하이픈 제거, 예: 01012345678)",
    )

    email = models.EmailField(null=True, blank=True)
    memo = models.TextField(null=True, blank=True)

    class Meta:
        ordering = ["-id"]
        indexes = [
            models.Index(fields=["tenant", "created_at"]),  # ✅ 복합 인덱스 추가
        ]
        constraints = [
            # ✅ tenant 단위 전화번호 유일성 (email 대신 phone 사용)
            models.UniqueConstraint(
                fields=["tenant", "phone"],
                name="uniq_parent_phone_per_tenant",
            ),
        ]

    def __str__(self):
        return f"{self.name} ({self.phone})"
