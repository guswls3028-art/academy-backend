from unittest.mock import patch
from unittest import skipUnless
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from django.contrib.auth import get_user_model
from django.db import connection, close_old_connections
from django.test import TestCase, TransactionTestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.models import Program, Tenant, TenantMembership
from apps.core.views.tenant_branding import TenantBrandingView
from apps.core.views.tenant_info import TenantInfoView


class SettingsConflictTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(code="qa-settings-conflict", name="Original")
        self.owner = get_user_model().objects.create_user(username="qa-settings-owner")
        TenantMembership.ensure_active(tenant=self.tenant, user=self.owner, role="owner")

    def request(self, view, method="get", data=None, **kwargs):
        request = getattr(APIRequestFactory(), method)("/", data, format="json")
        request.tenant = Tenant.objects.get(pk=self.tenant.pk)
        force_authenticate(request, user=self.owner)
        return view.as_view()(request, **kwargs)

    def test_stale_academy_edit_is_rejected_and_fresh_retry_preserves_other_branch(self):
        initial = self.request(TenantInfoView).data["academies"]
        first = [{"name": "Updated", "phone": "02-0000-0000"}, {"name": "Branch", "phone": ""}]
        response = self.request(TenantInfoView, "patch", {
            "academies": first, "expected_academies": initial,
        })
        self.assertEqual(response.status_code, 200, response.data)
        stale = self.request(TenantInfoView, "patch", {
            "academies": [{"name": "Lost branch", "phone": ""}],
            "expected_academies": initial, "og_title": "must not write",
        })
        self.assertEqual(stale.status_code, 409, stale.data)
        self.tenant.refresh_from_db()
        self.assertEqual(self.tenant.academies, first)
        self.assertEqual(self.tenant.og_title, "")
        edited = [{"name": "Retried", "phone": ""}, first[1]]
        response = self.request(TenantInfoView, "patch", {
            "academies": edited, "expected_academies": stale.data["academies"],
        })
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["academies"], edited)
        self.assertEqual(self.request(TenantInfoView).data["academies"], edited)

    @patch("apps.infrastructure.storage.r2.resolve_admin_logo_url", return_value="")
    def test_branding_stale_draft_conflicts_then_fresh_edit_succeeds(self, _logo):
        args = {"tenant_id": self.tenant.pk}
        initial = self.request(TenantBrandingView, **args).data
        expected = {key: initial[key] for key in ("displayName", "windowTitle", "loginTitle", "loginSubtitle")}
        first = self.request(TenantBrandingView, "patch", {"loginTitle": "First", "expected": expected}, **args)
        self.assertEqual(first.status_code, 200, first.data)
        stale = self.request(TenantBrandingView, "patch", {"displayName": "Stale", "expected": expected}, **args)
        self.assertEqual(stale.status_code, 409, stale.data)
        program = Program.objects.get(tenant=self.tenant)
        self.assertEqual(program.ui_config["login_title"], "First")
        self.assertEqual(program.display_name, initial["displayName"])
        expected["loginTitle"] = "First"
        retry = self.request(TenantBrandingView, "patch", {"displayName": "Retried", "expected": expected}, **args)
        self.assertEqual(retry.status_code, 200, retry.data)
        saved = self.request(TenantBrandingView, **args).data
        self.assertEqual(saved["loginTitle"], "First")
        self.assertEqual(saved["displayName"], "Retried")


@skipUnless(connection.vendor == "postgresql", "Requires PostgreSQL row locking")
class SettingsConcurrentSaveTests(TransactionTestCase):
    def test_two_owner_saves_from_same_snapshot_have_one_winner(self):
        tenant = Tenant.objects.create(code="qa-parallel-settings", name="Original")
        owner = get_user_model().objects.create_user(username="qa-parallel-owner")
        TenantMembership.ensure_active(tenant=tenant, user=owner, role="owner")
        barrier = Barrier(2)

        def save(name):
            close_old_connections()
            try:
                request = APIRequestFactory().patch("/", {
                    "expected_academies": [{"name": "Original", "phone": ""}],
                    "academies": [{"name": name, "phone": ""}],
                }, format="json")
                request.tenant = Tenant.objects.get(pk=tenant.pk)
                force_authenticate(request, user=owner)
                barrier.wait(timeout=10)
                response = TenantInfoView.as_view()(request)
                return name, response.status_code
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(save, ["First", "Second"]))
        self.assertEqual(sorted(status for _, status in results), [200, 409])
        tenant.refresh_from_db()
        winner = next(name for name, status in results if status == 200)
        self.assertEqual(tenant.academies, [{"name": winner, "phone": ""}])
