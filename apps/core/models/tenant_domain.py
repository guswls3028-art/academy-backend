# PATH: apps/core/models/tenant_domain.py
from __future__ import annotations

from django.db import models
from django.db.models import Q

from apps.core.models.tenant import Tenant


class TenantDomain(models.Model):
    """
    TenantDomain (Enterprise SSOT)

    ✅ 목적:
    - Tenant.code(식별자)와 Host(접속 도메인)를 분리하여 운영 유연성 확보
    - Resolver는 TenantDomain.host를 통해 tenant를 확정한다.

    🔒 봉인 원칙:
    - host 는 전역 unique (단일 테넌트에만 귀속)
    - tenant 당 is_primary=True 는 최대 1개 (DB 제약으로 강제)
    """

    tenant = models.ForeignKey(
        Tenant,
        on_delete=models.CASCADE,
        related_name="domains",
        null=False,
        blank=False,
    )

    host = models.CharField(
        max_length=255,
        unique=True,
        help_text="도메인/호스트(포트 제외, 소문자). 예: example.com, academy.example.com",
    )

    is_primary = models.BooleanField(
        default=True,
        help_text="대표 도메인 여부. tenant 당 최대 1개만 True 허용.",
    )

    is_active = models.BooleanField(
        default=True,
        help_text="운영 중인 도메인만 resolve 대상",
    )

    class Meta:
        app_label = "core"
        indexes = [
            models.Index(fields=["host"]),
            models.Index(fields=["tenant", "is_active"]),
        ]
        constraints = [
            # ✅ tenant 당 primary는 최대 1개
            models.UniqueConstraint(
                fields=["tenant"],
                condition=Q(is_primary=True),
                name="core_tenantdomain_one_primary_per_tenant",
            ),
        ]

    def __str__(self) -> str:
        return f"TenantDomain<{self.host}> -> {self.tenant.code}"
