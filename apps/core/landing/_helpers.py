"""apps/core/landing 패키지 공용 helper.

`_tenant_required` decorator — plain Django View 용 tenant 가드. manifest/sitemap/
hit_report_link 등에서 공용 사용.
"""
from django.http import JsonResponse


def tenant_required(view_func):
    """Plain Django view용 tenant 가드. request.tenant 없으면 400."""
    def wrapped(request, *args, **kwargs):
        if not getattr(request, "tenant", None):
            return JsonResponse({"detail": "Tenant required"}, status=400)
        return view_func(request, *args, **kwargs)
    return wrapped
