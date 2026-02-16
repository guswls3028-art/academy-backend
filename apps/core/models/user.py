# PATH: apps/core/models/user.py
from django.db import models
from django.conf import settings
from django.contrib.auth.models import AbstractUser, Group, Permission
from django.contrib.auth.validators import UnicodeUsernameValidator

from apps.core.models.base import TimestampModel
from apps.core.models.tenant import Tenant
from apps.core.db import TenantQuerySet


class User(AbstractUser):
    """
    Custom User 모델. 1테넌트 = 1프로그램 격리: username은 (tenant, username) 기준 유일.
    - tenant not null: 해당 테넌트 내에서만 username 유일 (다른 테넌트와 무관).
    - tenant null: 전역 관리자 등, username 전역 유일.
    """

    tenant = models.ForeignKey(
        Tenant,
        on_delete=models.CASCADE,
        related_name="users",
        null=True,
        blank=True,
        db_index=True,
    )
    username = models.CharField(
        max_length=150,
        unique=False,
        validators=[UnicodeUsernameValidator()],
        help_text="Required. 150 characters or fewer. Letters, digits and @/./+/-/_ only.",
    )
    name = models.CharField(max_length=50, blank=True, null=True)
    phone = models.CharField(
        max_length=20,
        blank=True,
        null=True,
        db_index=True,
        help_text="정규화된 전화번호 (하이픈 제거, 예: 01012345678)",
    )

    groups = models.ManyToManyField(
        Group,
        related_name="core_users",
        blank=True,
    )
    user_permissions = models.ManyToManyField(
        Permission,
        related_name="core_users",
        blank=True,
    )

    class Meta:
        app_label = "core"
        db_table = "accounts_user"
        ordering = ["-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "username"],
                condition=models.Q(tenant__isnull=False),
                name="core_user_tenant_username_uniq",
            ),
            models.UniqueConstraint(
                fields=["username"],
                condition=models.Q(tenant__isnull=True),
                name="core_user_username_global_uniq",
            ),
        ]

    def __str__(self):
        return self.username


class Attendance(TimestampModel):
    """
    ✅ 운영레벨 핵심:
    - Attendance는 tenant 단위로 격리되어야 함 (13+ 학원 전제)
    - tenant 없으면 조회/생성 불가(코드 레벨에서 강제)
    """

    objects = TenantQuerySet.as_manager()

    tenant = models.ForeignKey(
        Tenant,
        on_delete=models.CASCADE,
        related_name="attendances",
        null=False,
        blank=False,
        db_index=True,  # ✅ tenant_id 인덱스 추가
    )

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="attendances",
    )

    date = models.DateField()
    start_time = models.TimeField()
    end_time = models.TimeField()

    work_type = models.CharField(max_length=50)
    memo = models.TextField(blank=True, null=True)

    duration_hours = models.FloatField(default=0)
    amount = models.IntegerField(default=0)

    class Meta:
        app_label = "core"
        ordering = ["-date", "-start_time"]
        indexes = [
            models.Index(fields=["tenant", "date"]),  # ✅ 복합 인덱스 추가
        ]

    def __str__(self):
        return f"{self.user.username} - {self.date}"


class Expense(TimestampModel):
    """
    ✅ 운영레벨 핵심:
    - Expense도 tenant 단위로 격리되어야 함 (13+ 학원 전제)
    - tenant 없으면 조회/생성 불가(코드 레벨에서 강제)
    """

    objects = TenantQuerySet.as_manager()

    tenant = models.ForeignKey(
        Tenant,
        on_delete=models.CASCADE,
        related_name="expenses",
        null=False,
        blank=False,
        db_index=True,  # ✅ tenant_id 인덱스 추가
    )

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="expenses",
    )

    date = models.DateField()
    title = models.CharField(max_length=255)
    amount = models.IntegerField()
    memo = models.TextField(blank=True, null=True)

    class Meta:
        app_label = "core"
        ordering = ["-date"]
        indexes = [
            models.Index(fields=["tenant", "date"]),  # ✅ 복합 인덱스 추가
        ]

    def __str__(self):
        return f"{self.user.username} - {self.title}"
