from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.models import Program, Tenant, TenantMembership
from apps.core.views.program import ProgramView


class ProgramUpdateIntegrityTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(code="program-update-integrity", name="QA")
        self.owner = get_user_model().objects.create_user(username="program-update-owner")
        TenantMembership.ensure_active(tenant=self.tenant, user=self.owner, role="owner")
        self.program, _ = Program.objects.update_or_create(
            tenant=self.tenant, defaults={"display_name": "QA old", "brand_key": "qa-old"},
        )

    def update(self, data):
        request = APIRequestFactory().patch("/api/v1/core/program/", data, format="json")
        request.tenant = self.tenant
        force_authenticate(request, user=self.owner)
        return ProgramView.as_view()(request)

    def test_branding_patch_preserves_concurrent_cancellation_and_other_settings(self):
        stale = Program.objects.get(pk=self.program.pk)
        Program.objects.filter(pk=self.program.pk).update(
            cancel_at_period_end=True, brand_key="qa-new",
        )
        with patch("apps.core.views.program.core_repo.program_get_by_tenant", return_value=stale):
            response = self.update({"display_name": "QA new name"})
        self.assertEqual(response.status_code, 200, response.data)
        self.program.refresh_from_db()
        self.assertTrue(self.program.cancel_at_period_end)
        self.assertEqual(self.program.brand_key, "qa-new")
        self.assertEqual(self.program.display_name, "QA new name")
        self.assertEqual(response.data["display_name"], "QA new name")
        self.assertEqual(response.data["brand_key"], "qa-new")

    def test_invalid_setting_shapes_cannot_break_public_program_read(self):
        original_ui_config = self.program.ui_config.copy()
        original_feature_flags = self.program.feature_flags.copy()
        for data in (["display_name"], {"feature_flags": ["clinic"]}, {"feature_flags": "clinic"},
                     {"ui_config": ["logo"]}, {"ui_config": "logo"}):
            with self.subTest(data=data):
                response = self.update(data)
                self.assertEqual(response.status_code, 400, response.data)
                self.program.refresh_from_db()
                self.assertEqual(self.program.ui_config, original_ui_config)
                self.assertEqual(self.program.feature_flags, original_feature_flags)
        response = self.update({"ui_config": {"login_title": "QA academy"}})
        self.assertEqual(response.status_code, 200, response.data)
        self.program.refresh_from_db()
        self.assertEqual(self.program.ui_config["login_title"], "QA academy")
