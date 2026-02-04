# ======================================================================
# PATH: apps/core/db/tenant_queryset.py
# ======================================================================
from __future__ import annotations

from django.db import models

from apps.core.tenant.context import get_current_tenant


class TenantQuerySet(models.QuerySet):
    """
    Tenant-aware QuerySet (SSOT)

    규칙:
    - tenant가 resolve되지 않으면 결과는 항상 empty
    - domain 모델에 tenant FK가 붙는 순간 바로 사용 가능
    """

    def for_current_tenant(self):
        tenant = get_current_tenant()
        if tenant is None:
            return self.none()
        return self.filter(tenant=tenant)

    def require_tenant(self):
        """
        내부/운영 로직에서 tenant 강제용
        """
        tenant = get_current_tenant()
        if tenant is None:
            raise RuntimeError("Tenant is required but not resolved")
        return self.filter(tenant=tenant)
