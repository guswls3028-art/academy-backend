from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.core.models import Program, Tenant


@override_settings(
    ALLOWED_HOSTS=["api.hakwonplus.com", "testserver"],
    TENANT_HEADER_CODE_ALLOWED_HOSTS=("api.hakwonplus.com",),
)
class LegalConfigViewTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.ymath = Tenant.objects.create(name="Ymath", code="ymath")
        self.dnb = Tenant.objects.create(name="DNB", code="dnb")

        ymath_program = Program.objects.get(tenant=self.ymath)
        ymath_program.legal_company_name = "Ymath"
        ymath_program.legal_representative = "Y Owner"
        ymath_program.legal_business_number = "111-11-11111"
        ymath_program.legal_support_email = "ymath@example.com"
        ymath_program.save(update_fields=[
            "legal_company_name",
            "legal_representative",
            "legal_business_number",
            "legal_support_email",
        ])

        dnb_program = Program.objects.get(tenant=self.dnb)
        dnb_program.legal_company_name = "DnB"
        dnb_program.legal_representative = "D Owner"
        dnb_program.legal_business_number = "203-91-14509"
        dnb_program.legal_support_email = "dnb@example.com"
        dnb_program.save(update_fields=[
            "legal_company_name",
            "legal_representative",
            "legal_business_number",
            "legal_support_email",
        ])

    def _get_config(self, tenant_code):
        return self.client.get(
            "/api/v1/core/legal-config/",
            HTTP_HOST="api.hakwonplus.com",
            HTTP_X_TENANT_CODE=tenant_code,
        )

    def test_legal_config_is_scoped_to_resolved_tenant(self):
        response = self._get_config("ymath")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["X-Tenant-Code"], "ymath")
        self.assertEqual(response.json()["company_name"], "Ymath")
        self.assertEqual(response.json()["business_number"], "111-11-11111")
        self.assertNotEqual(response.json()["business_number"], "203-91-14509")

    def test_legal_config_is_not_browser_cached(self):
        response = self._get_config("ymath")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Cache-Control"], "private, no-store")

    def test_other_tenant_values_are_preserved(self):
        response = self._get_config("dnb")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["X-Tenant-Code"], "dnb")
        self.assertEqual(response.json()["company_name"], "DnB")
        self.assertEqual(response.json()["business_number"], "203-91-14509")
