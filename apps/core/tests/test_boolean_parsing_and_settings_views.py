from types import SimpleNamespace
from contextlib import nullcontext
from unittest.mock import patch

from django.test import SimpleTestCase
from rest_framework.exceptions import ValidationError

from apps.core.parsing import parse_bool
from apps.domains.clinic.views.settings_views import ClinicSettingsView
from apps.domains.students.views.registration_views import RegistrationRequestViewSet
from apps.core.views.tenant_management import TenantDetailView

class TestParseBool(SimpleTestCase):
    def test_parse_bool_false_string(self):
        self.assertFalse(parse_bool("false", field_name="flag"))
        self.assertFalse(parse_bool("0", field_name="flag"))

    def test_parse_bool_invalid_string_raises(self):
        with self.assertRaises(ValidationError):
            parse_bool("not-a-bool", field_name="flag")


class TestSettingsBooleanParsing(SimpleTestCase):
    def setUp(self):
        self.tenant = SimpleNamespace(
            student_registration_auto_approve=True,
            clinic_use_daily_random=True,
            clinic_auto_approve_booking=True,
            clinic_idcard_colors=["#ef4444", "#3b82f6", "#22c55e"],
        )
        self.tenant.save = lambda **kwargs: None

    def test_registration_settings_patch_false_string(self):
        request = SimpleNamespace(
            method="PATCH",
            data={"auto_approve": "false"},
            tenant=self.tenant,
        )
        viewset = RegistrationRequestViewSet()
        response = viewset.registration_settings(request)

        self.assertEqual(response.status_code, 200)
        self.assertFalse(self.tenant.student_registration_auto_approve)
        self.assertFalse(response.data["auto_approve"])

    def test_clinic_settings_patch_false_string(self):
        request = SimpleNamespace(
            method="PATCH",
            data={"use_daily_random": "false", "auto_approve_booking": "false"},
            tenant=self.tenant,
        )
        with patch("apps.domains.clinic.views.settings_views.transaction.atomic", return_value=nullcontext()):
            response = ClinicSettingsView().patch(request)

        self.assertEqual(response.status_code, 200)
        self.assertFalse(self.tenant.clinic_use_daily_random)
        self.assertFalse(self.tenant.clinic_auto_approve_booking)

    def test_tenant_detail_patch_false_string(self):
        request = SimpleNamespace(
            data={"isActive": "false"},
            tenant=SimpleNamespace(id=1),
        )
        tenant_obj = SimpleNamespace(name="t1", code="t1", is_active=True)
        tenant_obj.save = lambda **kwargs: None

        view = TenantDetailView()
        with patch("apps.core.views.tenant_management.is_platform_admin_tenant", return_value=True), \
             patch("apps.core.views.tenant_management.core_repo.tenant_get_by_id_any", return_value=tenant_obj), \
             patch("apps.core.views.tenant_management.record_audit"), \
             patch.object(TenantDetailView, "get", return_value=SimpleNamespace(status_code=200)):
            response = view.patch(request, tenant_id=1)

        self.assertEqual(response.status_code, 200)
        self.assertFalse(tenant_obj.is_active)
