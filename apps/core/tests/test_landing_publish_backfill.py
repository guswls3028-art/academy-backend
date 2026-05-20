from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.landing.config_helpers import SECTION_TYPES_ORDERED
from apps.core.landing.views_config import LandingAdminView, LandingPublishView
from apps.core.models import LandingPage, Tenant, TenantMembership

User = get_user_model()


def _legacy_draft() -> dict:
    return {
        "brand_name": "Legacy Academy",
        "tagline": "Reliable",
        "subtitle": "",
        "primary_color": "#2563EB",
        "cta_text": "Login",
        "cta_link": "/login",
        "contact": {"phone": "02-1234-5678", "email": "", "address": "Seoul"},
        "sections": [
            {"type": "hero", "enabled": True, "order": 0},
            {"type": "features", "enabled": True, "order": 1, "items": []},
            {"type": "contact", "enabled": True, "order": 2},
        ],
    }


def _required_section_types() -> set[str]:
    return {section_type for section_type in SECTION_TYPES_ORDERED if section_type != "notice"}


class LandingPublishBackfillTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.tenant = Tenant.objects.create(name="Landing Backfill", code="landing-backfill")
        self.owner = User.objects.create_user(
            username="landing-backfill-owner",
            password="pw1234",
            tenant=self.tenant,
            is_staff=True,
        )
        TenantMembership.ensure_active(tenant=self.tenant, user=self.owner, role="owner")

    def _auth_request(self, method: str, path: str, data: dict | None = None):
        request_method = getattr(self.factory, method)
        request = request_method(path, data or {}, format="json")
        request.tenant = self.tenant
        force_authenticate(request, user=self.owner)
        return request

    def test_put_persists_backfilled_sections(self):
        request = self._auth_request(
            "put",
            "/api/v1/core/landing/admin/",
            {"template_key": "minimal_tutor", "draft_config": _legacy_draft()},
        )

        response = LandingAdminView.as_view()(request)

        self.assertEqual(response.status_code, 200, response.data)
        landing = LandingPage.objects.get(tenant=self.tenant)
        saved_types = {section["type"] for section in landing.draft_config["sections"]}
        self.assertTrue(_required_section_types().issubset(saved_types))

    def test_publish_backfills_legacy_draft_before_snapshot(self):
        LandingPage.objects.create(
            tenant=self.tenant,
            template_key="minimal_tutor",
            draft_config=_legacy_draft(),
        )
        request = self._auth_request("post", "/api/v1/core/landing/publish/")

        response = LandingPublishView.as_view()(request)

        self.assertEqual(response.status_code, 200, response.data)
        landing = LandingPage.objects.get(tenant=self.tenant)
        published_types = {section["type"] for section in landing.published_config["sections"]}
        self.assertTrue(_required_section_types().issubset(published_types))
