# PATH: apps/core/models/tenant_membership.py
from __future__ import annotations

from django.conf import settings
from django.db import models, transaction

from apps.core.models.tenant import Tenant


class TenantMembership(models.Model):
    """
    User ↔ Tenant 관계 SSOT

    - 한 User는 여러 Tenant에 속할 수 있음 (M2M)
    - Tenant 내에서 role / 활성 상태를 명시
    - unique_together로 "같은 tenant에 중복 가입" 방지
    """

    ROLE_CHOICES = [
        ("owner", "Owner"),
        ("admin", "Admin"),
        ("teacher", "Teacher"),
        ("staff", "Staff"),
        ("student", "Student"),
        ("parent", "Parent"),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="tenant_memberships",
    )
    tenant = models.ForeignKey(
        Tenant,
        on_delete=models.CASCADE,
        related_name="memberships",
    )

    role = models.CharField(max_length=20, choices=ROLE_CHOICES)
    is_active = models.BooleanField(default=True)

    joined_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        app_label = "core"
        unique_together = ("user", "tenant")
        indexes = [
            models.Index(fields=["tenant", "user"]),
        ]

    def __str__(self) -> str:
        return f"{self.user} @ {self.tenant} ({self.role})"

    @classmethod
    @transaction.atomic
    def ensure_active(cls, *, tenant: Tenant, user, role: str) -> "TenantMembership":
        from academy.adapters.db.django import repositories_core as core_repo
        return core_repo.membership_ensure_active(tenant=tenant, user=user, role=role)
