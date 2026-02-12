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
        db_index=True,  # ✅ tenant_id 인덱스 추가
    )

    name = models.CharField(max_length=50)
    phone = models.CharField(
        max_length=20,
        null=True,
        blank=True,
        help_text="정규화된 전화번호 (하이픈 제거, 예: 01012345678)",
    )
    email = models.EmailField(null=True, blank=True)

    subject = models.CharField(max_length=50, null=True, blank=True)
    note = models.TextField(null=True, blank=True)

    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["-id"]
        indexes = [
            models.Index(fields=["tenant", "created_at"]),  # ✅ 복합 인덱스 추가
        ]
        constraints = [
            # ✅ tenant 단위 전화번호 유일성 (phone이 있는 경우만)
            models.UniqueConstraint(
                fields=["tenant", "phone"],
                condition=models.Q(phone__isnull=False) & ~models.Q(phone=""),
                name="uniq_teacher_phone_per_tenant",
            ),
        ]

    def __str__(self):
        return self.name
