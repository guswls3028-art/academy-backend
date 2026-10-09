import json

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken

from apps.core.models import LandingConsultRequest, Tenant, TenantMembership


class LandingConsultInboxTests(TestCase):
    url = "/api/v1/core/landing/admin/consult/"

    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(code="qa-consult-inbox", name="QA Inbox")
        cls.foreign = Tenant.objects.create(code="qa-consult-other", name="QA Other")
        cls.user = get_user_model().objects.create_user(username="qa-consult-owner", tenant=cls.tenant)
        TenantMembership.ensure_active(tenant=cls.tenant, user=cls.user, role="owner")
        now = timezone.now()
        cls.rows = LandingConsultRequest.objects.bulk_create([
            LandingConsultRequest(tenant=cls.tenant, name=f"QA {index:03d}", phone="01000000000",
                                  read_at=None if index < 5 else now, admin_memo="Original")
            for index in range(205)
        ])
        LandingConsultRequest.objects.filter(tenant=cls.tenant).update(created_at=now)
        cls.foreign_row = LandingConsultRequest.objects.create(tenant=cls.foreign, name="Other", phone="01000000000")

    def setUp(self):
        token = AccessToken.for_user(self.user)
        token["tenant_id"] = self.tenant.pk
        token["token_version"] = self.user.token_version or 0
        self.client = APIClient(HTTP_HOST="api.hakwonplus.com", HTTP_X_TENANT_CODE=self.tenant.code)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
        self.client.raise_request_exception = False

    def patch(self, body, item=None):
        return self.client.patch(f"{self.url}{(item or self.rows[0]).pk}/", json.dumps(body), content_type="application/json")

    def test_legacy_items_shape_and_full_summary_include_old_unread_requests(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data["items"]), 200)
        self.assertEqual(response.data["summary"], {"total": 205, "unread": 5})

    def test_all_pages_use_stable_tie_order_without_loss_or_foreign_rows(self):
        seen = []
        for page in range(1, 6):
            response = self.client.get(self.url, {"page": page, "page_size": 50})
            self.assertEqual(response.status_code, 200)
            seen.extend(row["id"] for row in response.data["items"])
            self.assertEqual(response.data["pagination"]["page"], page)
            self.assertEqual(response.data["pagination"]["has_next"], page < 5)
        self.assertEqual(seen, sorted((row.pk for row in self.rows), reverse=True))

    def test_unread_filter_and_out_of_range_page_recover_after_mark_read(self):
        response = self.client.get(self.url, {"filter": "unread", "page": 99, "page_size": 2})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["pagination"]["page"], 3)
        self.assertEqual([row["id"] for row in response.data["items"]], [self.rows[0].pk])
        self.assertEqual(self.patch({"mark_read": True}).status_code, 200)
        reloaded = self.client.get(self.url, {"filter": "unread", "page": 3, "page_size": 2})
        self.assertEqual(reloaded.data["pagination"]["page"], 2)
        self.assertEqual(reloaded.data["summary"], {"total": 205, "unread": 4})
        self.assertEqual(len(reloaded.data["items"]), 2)

    def test_summary_only_does_not_return_private_contact_rows(self):
        response = self.client.get(self.url, {"summary_only": "true"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["items"], [])
        self.assertEqual(response.data["summary"], {"total": 205, "unread": 5})

    def test_empty_filter_and_cleared_memo_keep_legacy_success_contract(self):
        self.assertEqual(self.patch({"mark_read": "false", "admin_memo": None}).status_code, 200)
        self.rows[0].refresh_from_db()
        self.assertIsNone(self.rows[0].read_at)
        self.assertEqual(self.rows[0].admin_memo, "")
        LandingConsultRequest.objects.filter(tenant=self.tenant).update(read_at=timezone.now())
        response = self.client.get(self.url, {"filter": "unread", "page": 99})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["items"], [])
        self.assertEqual(response.data["summary"], {"total": 205, "unread": 0})
        self.assertEqual(response.data["pagination"]["page"], 1)
        self.assertFalse(response.data["pagination"]["has_next"])

    def test_invalid_paging_and_patch_inputs_are_rejected_without_mutation(self):
        for params in ({"page": 0}, {"page": "nan"}, {"page": "1.0"}, {"page_size": 0}, {"page_size": 201}, {"filter": "unknown"}, {"summary_only": "yes"}):
            with self.subTest(params=params):
                self.assertEqual(self.client.get(self.url, params).status_code, 400)
        for body in ([], True, "value", {"mark_read": "invalid"}, {"admin_memo": {}}, {"admin_memo": "x" * 2001}):
            with self.subTest(body_type=type(body).__name__):
                self.assertEqual(self.patch(body).status_code, 400)
        self.rows[0].refresh_from_db()
        self.assertEqual(self.rows[0].admin_memo, "Original")
        self.assertIsNone(self.rows[0].read_at)

    def test_memo_save_does_not_reopen_a_concurrently_read_request(self):
        interleaved = False

        def concurrent_read(execute, sql, params, many, context):
            nonlocal interleaved
            if not interleaved and sql.lstrip().upper().startswith("UPDATE") and '"core_landing_consult_request"' in sql and '"admin_memo"' in sql:
                interleaved = True
                self.assertEqual(self.patch({"mark_read": True}).status_code, 200)
            return execute(sql, params, many, context)

        with connection.execute_wrapper(concurrent_read):
            self.assertEqual(self.patch({"admin_memo": "Saved memo"}).status_code, 200)
        self.assertTrue(interleaved)
        self.rows[0].refresh_from_db()
        self.assertIsNotNone(self.rows[0].read_at)
        self.assertEqual(self.rows[0].admin_memo, "Saved memo")
        original_read_at = self.rows[0].read_at
        self.assertEqual(self.patch({"mark_read": True}).status_code, 200)
        self.rows[0].refresh_from_db()
        self.assertEqual(self.rows[0].read_at, original_read_at)

    def test_current_role_and_tenant_scope_guard_both_reads_and_updates(self):
        self.assertEqual(self.patch({"admin_memo": "Wrong tenant"}, self.foreign_row).status_code, 404)
        TenantMembership.objects.filter(tenant=self.tenant, user=self.user).update(role="teacher")
        get_user_model().objects.filter(pk=self.user.pk).update(is_staff=True, is_superuser=True)
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.assertEqual(self.patch({"admin_memo": "Wrong role"}).status_code, 403)
        self.rows[0].refresh_from_db()
        self.assertEqual(self.rows[0].admin_memo, "Original")
