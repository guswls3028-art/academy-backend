from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken

from apps.core.models import Tenant, TenantMembership
from apps.domains.submissions.models import Submission


class PendingInboxPaginationTests(TestCase):
    url = "/api/v1/submissions/submissions/pending/"

    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(code="qa-submission-pages", name="QA Inbox")
        cls.foreign = Tenant.objects.create(code="qa-submission-foreign", name="QA Other")
        cls.user = get_user_model().objects.create_user(username="qa-submission-owner", tenant=cls.tenant)
        TenantMembership.ensure_active(tenant=cls.tenant, user=cls.user, role="owner")
        cls.rows = Submission.objects.bulk_create([
            Submission(tenant=cls.tenant, user=cls.user, target_type="exam", target_id=index + 1,
                       source="omr_scan", status="submitted") for index in range(205)
        ])
        cls.now = timezone.now()
        Submission.objects.filter(tenant=cls.tenant).update(created_at=cls.now)
        cls.other = Submission.objects.create(tenant=cls.foreign, user=cls.user, target_type="exam",
                                              target_id=1, source="omr_scan", status="submitted")

    def setUp(self):
        token = AccessToken.for_user(self.user)
        token["tenant_id"] = self.tenant.pk
        token["token_version"] = self.user.token_version or 0
        self.client = APIClient(HTTP_HOST="api.hakwonplus.com", HTTP_X_TENANT_CODE=self.tenant.code)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
        self.client.raise_request_exception = False

    def test_legacy_array_and_opt_in_pages_preserve_every_equal_timestamp_row(self):
        legacy = self.client.get(self.url, {"filter": "pending"})
        self.assertEqual(legacy.status_code, 200)
        self.assertIsInstance(legacy.data, list)
        self.assertEqual(len(legacy.data), 200)
        seen = []
        for page in range(1, 6):
            response = self.client.get(self.url, {"filter": "pending", "page": page, "page_size": 50})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.data["count"], 205)
            self.assertEqual(response.data["page"], page)
            self.assertEqual(response.data["has_next"], page < 5)
            seen.extend(row["id"] for row in response.data["results"])
        self.assertEqual(seen, sorted((row.pk for row in self.rows), reverse=True))

    def test_done_failed_and_pending_filters_apply_before_slicing(self):
        Submission.objects.filter(pk=self.rows[0].pk).update(status="done")
        Submission.objects.filter(pk=self.rows[1].pk).update(status="failed")
        for mode, target in (("done", self.rows[0]), ("failed", self.rows[1])):
            with self.subTest(mode=mode):
                response = self.client.get(self.url, {"filter": mode, "page": 1})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.data["count"], 1)
                self.assertEqual([row["id"] for row in response.data["results"]], [target.pk])
        pending = self.client.get(self.url, {"filter": "pending", "page": 1})
        self.assertEqual(pending.data["count"], 203)

    def test_discard_filters_and_summary_match_object_metadata(self):
        metadata = [{"discarded": {}}, {"discarded": {"reason": "duplicate"}},
                    {"discarded": None}, {"discarded": "legacy"}, {"discarded": []}, None]
        for row, meta in zip(self.rows, metadata):
            Submission.objects.filter(pk=row.pk).update(status="failed", meta=meta)
        for kind, ids in (("all", self.rows[:6]), ("discarded", self.rows[:2]), ("real_failed", self.rows[2:6])):
            with self.subTest(kind=kind):
                response = self.client.get(self.url, {"filter": "failed", "failed_type": kind, "page": 1})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.data["summary"], {"failed": 6, "real_failed": 4, "discarded": 2})
                self.assertEqual(response.data["count"], len(ids))
                self.assertEqual([row["id"] for row in response.data["results"]], sorted((row.pk for row in ids), reverse=True))

    def test_old_pending_remains_but_old_terminal_and_homework_do_not(self):
        old = self.now - timedelta(days=3)
        Submission.objects.filter(pk__in=[row.pk for row in self.rows[:3]]).update(created_at=old)
        Submission.objects.filter(pk=self.rows[1].pk).update(status="done")
        Submission.objects.filter(pk=self.rows[2].pk).update(status="failed")
        Submission.objects.filter(pk=self.rows[3].pk).update(target_type="homework")
        response = self.client.get(self.url, {"filter": "all", "page": 2, "page_size": 200})
        self.assertEqual(response.data["count"], 202)
        self.assertIn(self.rows[0].pk, [row["id"] for row in response.data["results"]])

    def test_page_clamps_after_last_pending_items_finish_without_writing(self):
        Submission.objects.filter(pk__in=[row.pk for row in self.rows[:5]]).update(status="done")
        before = list(Submission.objects.values_list("id", "status", "updated_at"))
        response = self.client.get(self.url, {"filter": "pending", "page": 5, "page_size": 50})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["page"], 4)
        self.assertEqual(response.data["pages"], 4)
        self.assertEqual(response.data["has_next"], False)
        self.assertEqual(list(Submission.objects.values_list("id", "status", "updated_at")), before)

    def test_bad_query_is_not_silently_an_all_filter(self):
        for query in ({"page": "1.0"}, {"page": 0}, {"page_size": 201}, {"page_size": -1},
                      {"filter": "unknown"}, {"failed_type": "unknown"}):
            with self.subTest(query=query):
                self.assertEqual(self.client.get(self.url, query).status_code, 400)

    def test_current_role_revocation_cannot_use_global_staff_flag(self):
        self.user.is_staff = True
        self.user.save(update_fields=["is_staff"])
        TenantMembership.objects.filter(tenant=self.tenant, user=self.user).update(role="student")
        self.assertEqual(self.client.get(self.url, {"page": 1}).status_code, 401)
        self.user.refresh_from_db()
        self.setUp()
        # A student without an active student profile is rejected during JWT
        # authentication; otherwise the staff permission returns 403.
        self.assertIn(self.client.get(self.url, {"page": 1}).status_code, (401, 403))
