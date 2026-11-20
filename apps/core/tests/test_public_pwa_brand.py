import json

from django.test import RequestFactory, TestCase
from rest_framework.test import APIRequestFactory

from apps.core.landing.views_manifest import LandingManifestView
from apps.core.models import Program, Tenant, TenantDomain
from apps.core.services.platform_push import build_platform_inbox_payload
from apps.core.views.tenant_info import PublicOgMetaView


class PublicPwaBrandTests(TestCase):
    def test_platform_install_and_push_use_the_current_brand_assets(self):
        request = RequestFactory().get("/api/v1/core/landing/manifest.json")
        request.tenant = Tenant.objects.create(name="학원플러스", code="hakwonplus")
        manifest = json.loads(LandingManifestView.as_view()(request).content)
        icons = {icon["sizes"]: icon["src"] for icon in manifest["icons"]}
        push = build_platform_inbox_payload(kind="bug", count=1)
        root = "/tenants/hakwonplus"
        version = "?v=20261119"
        self.assertEqual(icons, {
            f"{size}x{size}": f"https://hakwonplus.com{root}/pwa-{size}.png{version}"
            for size in (192, 512)
        })
        self.assertEqual(push["icon"], f"{root}/pwa-192.png{version}")
        self.assertEqual(push["badge"], f"{root}/apple-touch-icon.png{version}")

    def test_custom_tenant_exposes_its_uploaded_logo_for_pwa_install(self):
        tenant = Tenant.objects.create(
            name="새봄학원",
            code="new_academy",
            is_active=True,
        )
        TenantDomain.objects.filter(tenant=tenant).update(
            host="new-academy.example",
            is_active=True,
        )
        Program.objects.filter(tenant=tenant).update(
            ui_config={
                "logo_url": "https://cdn.example.com/tenant/new/logo.png",
                "window_title": "새봄학원",
            },
        )
        request = APIRequestFactory().get(
            "/api/v1/core/og-meta/",
            {"hostname": "new-academy.example"},
        )

        response = PublicOgMetaView.as_view()(request)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["title"], "새봄학원")
        self.assertEqual(response.data["tenant_code"], tenant.code)
        self.assertEqual(
            response.data["apple_touch_icon"],
            "https://cdn.example.com/tenant/new/logo.png",
        )
        self.assertNotIn("hakwonplus", str(response.data).lower())

        tenant.is_active = False
        tenant.save(update_fields=["is_active"])
        inactive = PublicOgMetaView.as_view()(request)
        self.assertEqual(inactive.data["title"], "")
        self.assertNotIn("tenant_code", inactive.data)
