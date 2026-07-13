# PATH: apps/core/models/user.py
import re

from django.db import models
from django.conf import settings
from django.contrib.auth.models import AbstractUser, Group, Permission

from apps.core.models.base import TimestampModel
from apps.core.models.tenant import Tenant
from apps.core.db import TenantQuerySet

# 테넌트별 격리: tenant 소속 User의 username은 DB에 "t{tenant_id}_{로그인아이디}" 형태로 저장 (전역 유일 유지).
USERNAME_TENANT_PREFIX = "t"


def user_internal_username(tenant, display_username: str) -> str:
    """저장용 username. tenant가 있으면 t{id}_{display} 로 전역 유일."""
    if not tenant or not (display_username or "").strip():
        return (display_username or "").strip()
    return f"{USERNAME_TENANT_PREFIX}{tenant.id}_{(display_username or '').strip()}"


def user_display_username(user) -> str:
    """Return the stable login identifier without a tenant storage prefix."""
    if not user or not getattr(user, "username", None):
        return ""
    uname = user.username
    # User.tenant is a mutable preferred-tenant pointer, not identity SSOT.
    match = re.match(r"^t\d+_(.+)$", uname)
    if match:
        return match.group(1)
    match = re.match(r"^p_\d+_(.+)$", uname)
    if match:
        return match.group(1)
    return uname


class User(AbstractUser):
    """
    Custom User. 1테넌트=1프로그램 격리: tenant 소속 시 username은 내부적으로 t{tenant_id}_{로그인아이디} 저장.
    - USERNAME_FIELD(username)는 DB에서 전역 유일 유지 (Django 요구사항).
    """

    tenant = models.ForeignKey(
        Tenant,
        on_delete=models.CASCADE,
        related_name="users",
        null=True,
        blank=True,
        db_index=True,
    )
    name = models.CharField(max_length=50, blank=True, null=True)
    phone = models.CharField(
        max_length=20,
        blank=True,
        null=True,
        db_index=True,
        help_text="정규화된 전화번호 (하이픈 제거, 예: 01012345678)",
    )
    must_change_password = models.BooleanField(
        default=False,
        help_text="True이면 로그인 후 비밀번호 변경 강제. 신규 학부모 계정 생성 시 설정.",
    )
    token_version = models.PositiveIntegerField(
        default=0,
        help_text="비밀번호 변경 시 +1. JWT claim과 비교하여 기존 토큰 무효화.",
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

    def __str__(self):
        return self.username


class PendingPasswordReset(models.Model):
    """
    Public account recovery password that is not activated until the user logs in
    with the delivered temporary password.

    This keeps the existing password usable if async Alimtalk delivery later
    fails after enqueue.
    """

    tenant = models.ForeignKey(
        Tenant,
        on_delete=models.CASCADE,
        related_name="pending_password_resets",
        db_index=True,
    )
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="pending_password_reset",
    )
    password_hash = models.CharField(max_length=128)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(db_index=True)

    class Meta:
        app_label = "core"
        db_table = "core_pending_password_reset"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["tenant", "expires_at"], name="core_ppr_tenant_expires_idx"),
        ]

    def __str__(self):
        return f"pending reset for user={self.user_id}"


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
