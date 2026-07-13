"""apps/core/landing 패키지 공용 helper.

- tenant_required: plain Django View 용 tenant 가드 (manifest/sitemap/hit_report_link 공용)
- check_landing_admin_role: owner/admin 만 랜딩 편집 (consult/testimonial/config admin 공용)
- client_ip: X-Forwarded-For 우선 client IP 추출
"""
from django.http import JsonResponse


LANDING_ADMIN_ROLES = {"owner", "admin"}


def tenant_required(view_func):
    """Plain Django view용 tenant 가드. request.tenant 없으면 400."""
    def wrapped(request, *args, **kwargs):
        if not getattr(request, "tenant", None):
            return JsonResponse({"detail": "Tenant required"}, status=400)
        return view_func(request, *args, **kwargs)
    return wrapped


def check_landing_admin_role(request) -> bool:
    """owner/admin만 랜딩 편집 허용. teacher/staff 차단."""
    from apps.core.models import TenantMembership
    tenant = request.tenant
    user = request.user
    try:
        membership = TenantMembership.objects.get(user=user, tenant=tenant, is_active=True)
    except TenantMembership.DoesNotExist:
        return False
    return membership.role in LANDING_ADMIN_ROLES


def client_ip(request) -> str:
    """Return the proxy-validated client IP."""
    from apps.core.services.client_ip import get_client_ip

    return get_client_ip(request) or "unknown"
