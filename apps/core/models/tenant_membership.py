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

    # ------------------------------------------------------------------
    # ✅ 운영 SSOT helper
    # - “학생/부모 role 연결”을 테넌트 단위로 항상 일관되게 만드는 최소 API
    # - 도메인에서 user만 있으면 이걸 호출해서 membership을 확정하면 됨
    # ------------------------------------------------------------------
    @classmethod
    @transaction.atomic
    def ensure_active(cls, *, tenant: Tenant, user, role: str) -> "TenantMembership":
        role = str(role).strip().lower()
        allowed = {c[0] for c in cls.ROLE_CHOICES}
        if role not in allowed:
            raise ValueError(f"invalid role: {role}")

        obj = cls.objects.select_for_update().filter(tenant=tenant, user=user).first()
        if obj:
            # role 변경은 “운영 정책” 영역이라 여기서 강제 변경하지 않음.
            # 단, 비활성이면 활성화는 SSOT로 허용
            if not obj.is_active:
                obj.is_active = True
                obj.save(update_fields=["is_active"])
            return obj

        return cls.objects.create(
            tenant=tenant,
            user=user,
            role=role,
            is_active=True,
        )
