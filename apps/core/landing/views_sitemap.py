"""LandingSitemapView — tenant별 sitemap.xml (랜딩 + 적중보고서 URL 모음).

분리 출처: apps/core/views_landing.py:1084-1118 (2026-05-14 P1 audit 패키지 분리).
"""
from django.http import HttpResponse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from apps.core.models import LandingPage

from ._helpers import tenant_required


@method_decorator([csrf_exempt, tenant_required], name="dispatch")
class LandingSitemapView(View):
    """GET /api/v1/core/landing/sitemap.xml — 학원 홈페이지 + 적중 보고서 URL 모음.

    plain Django View — DRF renderer 미사용 (xml 직접 반환).
    """

    def get(self, request):
        host = request.get_host()
        scheme = "https"
        urls = [f"{scheme}://{host}/landing"]
        try:
            lp = LandingPage.objects.get(tenant=request.tenant, is_published=True)
            pub = lp.published_config or {}
            for sec in (pub.get("sections") or []):
                if sec.get("type") == "hit_reports" and sec.get("enabled"):
                    items = sec.get("items") or []
                    if items:
                        urls.append(f"{scheme}://{host}/landing/reports")
                        for it in items:
                            rid = it.get("report_id")
                            if isinstance(rid, int):
                                urls.append(f"{scheme}://{host}/landing/reports/{rid}")
                    break
        except LandingPage.DoesNotExist:
            pass

        body = '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        for u in urls:
            body += f"  <url><loc>{u}</loc></url>\n"
        body += "</urlset>\n"
        resp = HttpResponse(body, content_type="application/xml; charset=utf-8")
        resp["Cache-Control"] = "public, max-age=600"
        return resp
