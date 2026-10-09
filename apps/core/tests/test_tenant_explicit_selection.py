import json

from django.http import JsonResponse
from django.test import RequestFactory, TestCase, override_settings
from rest_framework.test import APIClient

from apps.core.middleware.tenant import TenantMiddleware
from apps.core.models import Tenant, TenantDomain
from apps.core.tenant.context import get_current_tenant


@override_settings(
    ALLOWED_HOSTS=["api.hakwonplus.com", "qa-academy.example", "qa.elb.amazonaws.com"],
    TENANT_HEADER_CODE_ALLOWED_HOSTS=("api.hakwonplus.com",),
    BILLING_TEST_BYPASS_SUBSCRIPTION=True,
)
class ExplicitTenantSelectionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.host_tenant = Tenant.objects.create(name="QA host", code="qa-host-tenant")
        TenantDomain.objects.update_or_create(
            tenant=cls.host_tenant,
            is_primary=True,
            defaults={"host": "api.hakwonplus.com", "is_active": True},
        )
        TenantDomain.objects.create(
            tenant=cls.host_tenant, host="qa-academy.example", is_primary=False,
        )
        cls.selected = Tenant.objects.create(name="QA selected", code="qa-selected")
        cls.inactive = Tenant.objects.create(name="QA inactive", code="qa-inactive", is_active=False)

    def request(self, *, host="api.hakwonplus.com", path="/api/v1/core/program/", header=None):
        self.visited = []

        def view(request):
            self.visited.append(get_current_tenant())
            return JsonResponse({"tenant": request.tenant.pk if request.tenant else None})

        extra = {"HTTP_HOST": host}
        if header is not None:
            extra["HTTP_X_TENANT_CODE"] = header
        response = TenantMiddleware(view)(RequestFactory().get(path, **extra))
        self.assertIsNone(get_current_tenant())
        return response

    def assert_rejected(self, response):
        self.assertEqual(response.status_code, 404)
        self.assertEqual(json.loads(response.content)["code"], "tenant_invalid")
        self.assertEqual(self.visited, [])
        self.assertNotIn("X-Tenant-Id", response)

    def test_invalid_header_never_falls_back_to_active_host(self):
        for code in ("qa-missing", self.inactive.code, "", "   "):
            with self.subTest(code=code):
                self.assert_rejected(self.request(header=code))

    def test_invalid_header_does_not_fall_back_to_valid_public_query(self):
        self.assert_rejected(self.request(
            header="qa-missing",
            path=f"/api/v1/landing-public/resources/?tenant={self.selected.code}",
        ))

    def test_active_header_selects_tenant_and_normalizes_code(self):
        response = self.request(header=f" {self.selected.code.upper()} ")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(response.content)["tenant"], self.selected.pk)
        self.assertEqual(response["X-Tenant-Code"], self.selected.code)
        self.assertEqual(self.visited, [self.selected])

    def test_absent_header_preserves_host_selection(self):
        response = self.request()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(response.content)["tenant"], self.host_tenant.pk)

    def test_custom_host_remains_authoritative_over_header(self):
        for code in (self.selected.code, "qa-missing", ""):
            with self.subTest(code=code):
                response = self.request(host="qa-academy.example", header=code)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(json.loads(response.content)["tenant"], self.host_tenant.pk)

    def test_invalid_public_query_never_falls_back_to_host(self):
        for code in ("qa-missing", self.inactive.code, "", "%20"):
            with self.subTest(code=code):
                self.assert_rejected(self.request(path=f"/api/v1/landing-public/resources/?tenant={code}"))

    def test_public_query_keeps_native_document_reading_available(self):
        response = self.request(path=f"/api/v1/landing-public/resources/?tenant={self.selected.code}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(response.content)["tenant"], self.selected.pk)

    def test_non_public_query_cannot_select_tenant(self):
        response = self.request(path=f"/api/v1/core/program/?tenant={self.selected.code}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(response.content)["tenant"], self.host_tenant.pk)

    def test_valid_header_stays_authoritative_over_public_query(self):
        response = self.request(header=self.selected.code, path="/api/v1/landing-public/resources/?tenant=qa-missing")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(response.content)["tenant"], self.selected.pk)

    def test_unmapped_alb_host_rejects_invalid_explicit_header(self):
        for code in ("qa-missing", self.inactive.code, ""):
            with self.subTest(code=code):
                self.assert_rejected(self.request(host="qa.elb.amazonaws.com", header=code))

    def test_unmapped_alb_host_accepts_valid_header(self):
        response = self.request(host="qa.elb.amazonaws.com", header=self.selected.code)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(response.content)["tenant"], self.selected.pk)

    def test_program_api_returns_selected_brand_and_rejects_invalid_selection(self):
        program = self.selected.program
        program.display_name = "QA selected brand"
        program.save(update_fields=["display_name"])
        client = APIClient()
        response = client.get("/api/v1/core/program/", HTTP_HOST="api.hakwonplus.com", HTTP_X_TENANT_CODE=self.selected.code)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["display_name"], "QA selected brand")
        rejected = client.get("/api/v1/core/program/", HTTP_HOST="api.hakwonplus.com", HTTP_X_TENANT_CODE="qa-missing")
        self.assertEqual(rejected.status_code, 404)
        self.assertEqual(rejected.json()["code"], "tenant_invalid")
