from django.test import TestCase
from rest_framework.test import APIRequestFactory

from apps.core.models import Program, Tenant, TenantDomain
from apps.core.views.tenant_info import PublicOgMetaView


class PublicPwaBrandTests(TestCase):
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
        self.assertEqual(
            response.data["apple_touch_icon"],
            "https://cdn.example.com/tenant/new/logo.png",
        )
        self.assertNotIn("hakwonplus", str(response.data).lower())
