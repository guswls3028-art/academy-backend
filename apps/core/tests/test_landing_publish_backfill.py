from copy import deepcopy
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.landing.config_helpers import SECTION_TYPES_ORDERED
from apps.core.landing.views_config import LandingAdminView, LandingPublishView
from apps.core.landing.views_hit_report import (
    LandingHitReportError,
    toggle_hit_report_on_landing,
)
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

    def test_publish_prewarms_hit_reports_before_snapshot(self):
        draft = _legacy_draft()
        draft["sections"].append(
            {
                "type": "hit_reports",
                "enabled": True,
                "order": 3,
                "items": [{"report_id": 42}],
            },
        )
        LandingPage.objects.create(
            tenant=self.tenant,
            template_key="minimal_tutor",
            draft_config=draft,
        )
        request = self._auth_request("post", "/api/v1/core/landing/publish/")

        with patch(
            "apps.core.landing.views_hit_report."
            "prewarm_hit_report_previews_for_landing",
            return_value=1,
        ) as prewarm:
            response = LandingPublishView.as_view()(request)

        self.assertEqual(response.status_code, 200, response.data)
        prewarm.assert_called_once()

    def test_publish_stays_private_when_hit_report_preview_fails(self):
        LandingPage.objects.create(
            tenant=self.tenant,
            template_key="minimal_tutor",
            draft_config=_legacy_draft(),
        )
        request = self._auth_request("post", "/api/v1/core/landing/publish/")

        with patch(
            "apps.core.landing.views_hit_report."
            "prewarm_hit_report_previews_for_landing",
            side_effect=LandingHitReportError(
                503,
                "대표 비교 화면 준비 실패",
                code="preview_prepare_failed",
            ),
        ):
            response = LandingPublishView.as_view()(request)

        self.assertEqual(response.status_code, 503, response.data)
        landing = LandingPage.objects.get(tenant=self.tenant)
        self.assertFalse(landing.is_published)

    def test_publish_returns_conflict_when_draft_changes_during_prewarm(self):
        LandingPage.objects.create(
            tenant=self.tenant,
            template_key="minimal_tutor",
            draft_config=_legacy_draft(),
        )
        request = self._auth_request("post", "/api/v1/core/landing/publish/")

        def mutate_draft(_tenant, prepared_config):
            changed = deepcopy(prepared_config)
            changed["tagline"] = "Changed while preparing previews"
            LandingPage.objects.filter(tenant=self.tenant).update(
                draft_config=changed,
            )
            return 0

        with patch(
            "apps.core.landing.views_hit_report."
            "prewarm_hit_report_previews_for_landing",
            side_effect=mutate_draft,
        ):
            response = LandingPublishView.as_view()(request)

        self.assertEqual(response.status_code, 409, response.data)
        landing = LandingPage.objects.get(tenant=self.tenant)
        self.assertFalse(landing.is_published)

    def test_toggle_prewarms_every_report_in_resulting_landing(self):
        draft = _legacy_draft()
        draft["sections"].append(
            {
                "type": "hit_reports",
                "enabled": True,
                "order": 3,
                "items": [{"report_id": 7}],
            },
        )
        LandingPage.objects.create(
            tenant=self.tenant,
            template_key="minimal_tutor",
            draft_config=draft,
        )

        with (
            patch(
                "apps.domains.matchup.models.MatchupHitReport.objects.filter",
            ) as report_filter,
            patch(
                "apps.core.landing.views_hit_report."
                "prewarm_hit_report_previews_for_landing",
                return_value=2,
            ) as prewarm,
        ):
            report_filter.return_value.exists.return_value = True
            result = toggle_hit_report_on_landing(
                self.tenant,
                8,
                action="add",
                auto_publish=True,
            )

        self.assertTrue(result["published"])
        prepared_config = prewarm.call_args.args[1]
        hit_section = next(
            section
            for section in prepared_config["sections"]
            if section["type"] == "hit_reports"
        )
        self.assertEqual(
            {item["report_id"] for item in hit_section["items"]},
            {7, 8},
        )

    def test_toggle_does_not_overwrite_concurrent_draft_change(self):
        draft = _legacy_draft()
        draft["sections"].append(
            {
                "type": "hit_reports",
                "enabled": True,
                "order": 3,
                "items": [{"report_id": 7}],
            },
        )
        LandingPage.objects.create(
            tenant=self.tenant,
            template_key="minimal_tutor",
            draft_config=draft,
        )

        def mutate_draft(_tenant, prepared_config):
            changed = deepcopy(prepared_config)
            changed["tagline"] = "Concurrent owner edit"
            LandingPage.objects.filter(tenant=self.tenant).update(
                draft_config=changed,
            )
            return 2

        with (
            patch(
                "apps.domains.matchup.models.MatchupHitReport.objects.filter",
            ) as report_filter,
            patch(
                "apps.core.landing.views_hit_report."
                "prewarm_hit_report_previews_for_landing",
                side_effect=mutate_draft,
            ),
            self.assertRaises(LandingHitReportError) as raised,
        ):
            report_filter.return_value.exists.return_value = True
            toggle_hit_report_on_landing(
                self.tenant,
                8,
                action="add",
                auto_publish=True,
            )

        self.assertEqual(raised.exception.status_code, 409)
        landing = LandingPage.objects.get(tenant=self.tenant)
        self.assertEqual(landing.draft_config["tagline"], "Concurrent owner edit")
        self.assertFalse(landing.is_published)
