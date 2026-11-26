from types import SimpleNamespace

from django.test import SimpleTestCase, override_settings

from apps.core.dawon_nonlecture_operations import allows_nonlecture_operation


@override_settings(DAWON_REVIEW_CLASS_GROUPS=True)
class DawonNonlectureOperationsTests(SimpleTestCase):
    def setUp(self):
        self.tenant = SimpleNamespace(pk=719, code="dawon")
        self.path = "/api/v1/clinic/settings/"

    def allowed(self, method, data=None, path=None):
        return allows_nonlecture_operation(SimpleNamespace(
            tenant=self.tenant, method=method, path=path or self.path, data=data,
        ))

    def test_native_clinic_settings_reads_and_known_changes(self):
        self.assertTrue(self.allowed("GET"))
        self.assertTrue(self.allowed("HEAD"))
        self.assertTrue(self.allowed("PATCH", {"colors": ["#000000"] * 3}))
        self.assertTrue(self.allowed("PATCH", {"booking_mode": "time_range"}))

    def test_rejects_unrelated_paths_methods_and_fields(self):
        for method, data, path in [
            ("POST", {"colors": []}, self.path),
            ("PATCH", {}, self.path),
            ("PATCH", {"colors": [], "tenant": 849}, self.path),
            ("PATCH", {"provider_key": "invalid"}, self.path),
            ("PATCH", {"colors": []}, "/api/v1/clinic/settings/1/"),
            ("GET", None, "/api/v1/messaging/templates/"),
        ]:
            with self.subTest(method=method, path=path, data=data):
                self.assertFalse(self.allowed(method, data, path))

    def test_rejects_wrong_or_missing_physical_tenant(self):
        for tenant in [
            None, SimpleNamespace(pk=849, code="dawon"),
            SimpleNamespace(pk=719, code="hakwonplus"),
        ]:
            self.tenant = tenant
            self.assertFalse(self.allowed("GET"))

    def test_native_bundle_mutations_use_exact_routes(self):
        root = "/api/v1/exams/bundles/"
        for method, route in [("POST", root), ("PUT", root + "2/"),
                              ("DELETE", root + "2/"), ("POST", root + "2/apply/")]:
            with self.subTest(method=method, route=route):
                self.assertTrue(self.allowed(method, path=route))
        for method, route in [("DELETE", root), ("PATCH", root + "2/"),
                              ("POST", root + "2/"), ("DELETE", root + "2/apply/")]:
            with self.subTest(method=method, route=route):
                self.assertFalse(self.allowed(method, path=route))

    @override_settings(DAWON_REVIEW_CLASS_GROUPS=False)
    def test_rejects_live_settings(self):
        self.assertFalse(self.allowed("GET"))
