# PATH: apps/core/views/dev_search.py
"""
/dev 글로벌 검색 (Cmd+K 팔레트).

질의:
  GET /api/v1/core/dev/search/?q=<keyword>&limit=10
응답:
  {
    "tenants": [{ id, code, name, primary_domain, is_active }, ...],
    "users":   [{ id, username, name, phone, tenant_id, tenant_code, role }, ...],
  }

크로스테넌트 USER 검색이 노출되므로 IsPlatformAdmin 필수.
"""
from django.contrib.auth import get_user_model
from django.db.models import Q
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.models import Tenant, TenantDomain, TenantMembership
from apps.core.permissions import IsPlatformAdmin


class DevGlobalSearchView(APIView):
    permission_classes = [IsAuthenticated, IsPlatformAdmin]

    def get(self, request):
        q = (request.query_params.get("q") or "").strip()
        try:
            limit = max(1, min(50, int(request.query_params.get("limit") or 10)))
        except (TypeError, ValueError):
            limit = 10

        if not q:
            return Response({"tenants": [], "users": []})

        # ── Tenants ──
        tenant_qs = Tenant.objects.filter(
            Q(code__icontains=q) | Q(name__icontains=q),
        )[: limit * 2]
        # 도메인 일치도 검색
        domain_tenants = list(
            TenantDomain.objects.filter(host__icontains=q).select_related("tenant").values_list("tenant", flat=True)
        )
        if domain_tenants:
            extra = Tenant.objects.filter(id__in=domain_tenants).exclude(
                id__in=[t.id for t in tenant_qs],
            )[:limit]
            tenant_list = list(tenant_qs) + list(extra)
        else:
            tenant_list = list(tenant_qs)

        domain_map: dict = {}
        for td in TenantDomain.objects.filter(tenant__in=tenant_list, is_active=True, is_primary=True):
            domain_map[td.tenant_id] = td.host

        tenants_data = [
            {
                "id": t.id,
                "code": t.code,
                "name": t.name,
                "primary_domain": domain_map.get(t.id),
                "is_active": t.is_active,
            }
            for t in tenant_list[:limit]
        ]

        # ── Users (크로스 테넌트) ──
        User = get_user_model()
        user_qs = (
            User.objects.filter(
                Q(username__icontains=q) | Q(name__icontains=q) | Q(phone__icontains=q),
                tenant__isnull=False,
            )
            .select_related("tenant")
            .order_by("-id")[:limit]
        )
        user_ids = [u.id for u in user_qs]
        # 멤버십 role 매핑
        memberships = TenantMembership.objects.filter(
            user_id__in=user_ids, is_active=True,
        ).values("user_id", "tenant_id", "role")
        role_map = {(m["user_id"], m["tenant_id"]): m["role"] for m in memberships}

        users_data = [
            {
                "id": u.id,
                "username": getattr(u, "username", "") or "",
                "name": getattr(u, "name", "") or "",
                "phone": getattr(u, "phone", "") or "",
                "tenant_id": u.tenant_id,
                "tenant_code": getattr(u.tenant, "code", "") if u.tenant_id else "",
                "tenant_name": getattr(u.tenant, "name", "") if u.tenant_id else "",
                "role": role_map.get((u.id, u.tenant_id)) or "",
                "is_active": u.is_active,
            }
            for u in user_qs
        ]

        return Response({
            "tenants": tenants_data,
            "users": users_data,
        })
