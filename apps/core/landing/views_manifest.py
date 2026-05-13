"""LandingManifestView — tenant별 동적 PWA manifest.json.

분리 출처: apps/core/views_landing.py:1037-1081 (2026-05-14 P1 audit 패키지 분리).
"""
from django.http import JsonResponse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from apps.core.models import LandingPage

from ._helpers import tenant_required


@method_decorator([csrf_exempt, tenant_required], name="dispatch")
class LandingManifestView(View):
    """GET /api/v1/core/landing/manifest.json — tenant별 동적 PWA manifest.

    frontend가 link rel="manifest" href="/api/v1/core/landing/manifest.json" 로딩.
    brand_name + theme_color + icons.
    """

    def get(self, request):
        brand = "학원플러스"
        theme_color = "#D4A04C"
        icon_192 = "/teacher-icons/icon-192.png"
        icon_512 = "/teacher-icons/icon-512.png"
        try:
            lp = LandingPage.objects.get(tenant=request.tenant, is_published=True)
            cfg = lp.published_config or {}
            brand = (cfg.get("brand_name") or brand).strip()[:40]
            tc = (cfg.get("primary_color") or "").strip()
            if tc.startswith("#") and len(tc) in (4, 7):
                theme_color = tc
            logo = (cfg.get("logo_url") or "").strip()
            if logo and logo.startswith(("http://", "https://", "/")):
                icon_192 = logo
                icon_512 = logo
        except LandingPage.DoesNotExist:
            pass
        manifest = {
            "name": f"{brand} — 우리 학원 커뮤니티",
            "short_name": brand[:12],
            "description": f"{brand} 학원 가족을 위한 커뮤니티·매치업·강의 플랫폼",
            "start_url": "/landing",
            "scope": "/",
            "display": "standalone",
            "orientation": "portrait",
            "background_color": "#0A0E1A",
            "theme_color": theme_color,
            "lang": "ko-KR",
            "categories": ["education", "social"],
            "icons": [
                {"src": icon_192, "sizes": "192x192", "type": "image/png", "purpose": "any maskable"},
                {"src": icon_512, "sizes": "512x512", "type": "image/png", "purpose": "any maskable"},
            ],
        }
        resp = JsonResponse(manifest)
        resp["Cache-Control"] = "public, max-age=300"
        return resp
