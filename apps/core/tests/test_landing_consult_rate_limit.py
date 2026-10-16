from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Event, Lock
from time import monotonic, sleep
from unittest.mock import patch

from django.db import DatabaseError, close_old_connections, connection
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.core.landing.views_consult import consult_phone_identity, is_rate_limited
from apps.core.models import LandingConsultRequest, PlatformPushOutbox, Tenant


def submit(client, tenant, phone="010-0000-0000", **extra):
    return client.post(
        "/api/v1/core/landing/consult/",
        {"name": "상담 QA", "phone": phone, "source": "promo-contact", "message": "입력 원문",
         "privacy_agreed": True, "privacy_policy_version": "1.2", **extra},
        format="json", HTTP_HOST="api.hakwonplus.com", HTTP_X_TENANT_CODE=tenant.code,
    )


@override_settings(ALLOWED_HOSTS=["api.hakwonplus.com", "testserver"],
                   TENANT_HEADER_CODE_ALLOWED_HOSTS=("api.hakwonplus.com",), OWNER_TENANT_ID=0)
class LandingConsultRateLimitTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.tenant = Tenant.objects.create(name="문의 QA", code="consult-limit-qa")

    def seed(self, phone="010-0000-0000", tenant=None, count=5):
        LandingConsultRequest.objects.bulk_create([
            LandingConsultRequest(tenant=tenant or self.tenant, name="QA", phone=phone) for _ in range(count)
        ])

    def test_formats_share_budget_without_rewriting_data(self):
        variants = ["010-0000-0000", "010 0000 0000", "01000000000", "+82 10 0000 0000", "010.0000.0000"]
        for phone in variants:
            response = submit(self.client, self.tenant, phone)
            self.assertEqual(response.status_code, 201)
            saved = LandingConsultRequest.objects.get(pk=response.data["id"])
            self.assertEqual(saved.phone, phone)
            self.assertEqual(saved.message, "입력 원문")
            self.assertTrue(saved.privacy_agreed)
        self.assertEqual(submit(self.client, self.tenant, "+82(0)10-0000-0000").status_code, 429)
        self.assertEqual(LandingConsultRequest.objects.count(), 5)
        self.assertCountEqual(list(LandingConsultRequest.objects.values_list("phone", flat=True)), variants)

    def test_legacy_unicode_phone_shares_budget_but_other_number_does_not(self):
        self.seed(phone="０１０ ００００ ００００")
        self.assertEqual(submit(self.client, self.tenant).status_code, 429)
        self.assertEqual(submit(self.client, self.tenant, "010-0000-0001").status_code, 201)
        self.assertEqual(consult_phone_identity("+82 (0)10-0000-0000"), "01000000000")

    def test_other_tenant_and_expired_rows_do_not_consume_budget(self):
        other = Tenant.objects.create(name="다른 학원", code="consult-other-qa")
        self.seed(tenant=other)
        self.seed()
        LandingConsultRequest.objects.filter(tenant=self.tenant).update(created_at=timezone.now() - timedelta(seconds=61))
        self.assertEqual(submit(self.client, self.tenant).status_code, 201)
        self.assertEqual(LandingConsultRequest.objects.filter(tenant=other).count(), 5)

    def test_database_failure_is_visible_and_retry_preserves_request(self):
        with patch("apps.core.landing.views_consult.is_rate_limited", side_effect=DatabaseError("isolated failure")):
            response = submit(self.client, self.tenant)
        self.assertEqual(response.status_code, 503)
        self.assertIn("잠시 후 다시 시도", response.data["detail"])
        self.assertFalse(LandingConsultRequest.objects.exists())
        self.assertFalse(PlatformPushOutbox.objects.exists())
        self.assertEqual(submit(self.client, self.tenant).status_code, 201)
        self.assertEqual(LandingConsultRequest.objects.get().message, "입력 원문")

    def test_overlong_phone_and_non_object_payload_rejected_before_lookup(self):
        with patch("apps.core.landing.views_consult.is_rate_limited") as limiter:
            self.assertEqual(submit(self.client, self.tenant, "01000000000" + " " * 8 + "()").status_code, 400)
            response = self.client.post("/api/v1/core/landing/consult/", ["invalid"], format="json",
                                        HTTP_HOST="api.hakwonplus.com", HTTP_X_TENANT_CODE=self.tenant.code)
            self.assertEqual(response.status_code, 400)
            limiter.assert_not_called()
        self.assertFalse(LandingConsultRequest.objects.exists())

    def test_outbox_failure_rolls_back_created_request(self):
        with override_settings(OWNER_TENANT_ID=self.tenant.pk), patch(
            "apps.core.signals._queue_platform_push", side_effect=DatabaseError("isolated outbox failure")
        ):
            response = submit(self.client, self.tenant)
        self.assertEqual(response.status_code, 503)
        self.assertFalse(LandingConsultRequest.objects.exists())
        self.assertFalse(PlatformPushOutbox.objects.exists())

    def test_missing_tenant_and_honeypot_never_create_requests(self):
        response = self.client.post("/api/v1/core/landing/consult/", {}, format="json", HTTP_HOST="api.hakwonplus.com")
        self.assertIn(response.status_code, (400, 403, 404))
        with patch("apps.core.landing.views_consult.is_rate_limited") as limiter:
            self.assertEqual(submit(self.client, self.tenant, website="bot").status_code, 201)
            limiter.assert_not_called()
        self.assertFalse(LandingConsultRequest.objects.exists())


@override_settings(ALLOWED_HOSTS=["api.hakwonplus.com", "testserver"],
                   TENANT_HEADER_CODE_ALLOWED_HOSTS=("api.hakwonplus.com",), OWNER_TENANT_ID=0)
class LandingConsultConcurrentLimitTests(TransactionTestCase):
    def test_concurrent_fifth_and_sixth_request_are_serialized_on_postgresql(self):
        if connection.vendor != "postgresql":
            self.skipTest("Row-lock concurrency is verified by the required PostgreSQL job")
        tenant = Tenant.objects.create(name="동시 문의 QA", code="consult-concurrent-qa")
        LandingConsultRequest.objects.bulk_create([
            LandingConsultRequest(tenant=tenant, name="QA", phone="01000000000") for _ in range(4)
        ])
        first_checked, release_first, second_started = Event(), Event(), Event()
        call_lock = Lock()
        calls, pids = [], {}

        def checked(*args, **kwargs):
            result = is_rate_limited(*args, **kwargs)
            with call_lock:
                calls.append(result)
                first_call = len(calls) == 1
            if first_call:
                first_checked.set()
                if not release_first.wait(15):
                    raise AssertionError("Test did not release the first request")
            return result

        def post_in_connection(label, phone):
            close_old_connections()
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_backend_pid()")
                    pids[label] = cursor.fetchone()[0]
                if label == "second":
                    second_started.set()
                return submit(APIClient(), tenant, phone).status_code
            finally:
                close_old_connections()

        with patch("apps.core.landing.views_consult.is_rate_limited", side_effect=checked), ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(post_in_connection, "first", "010-0000-0000")
            try:
                self.assertTrue(first_checked.wait(10))
                second = pool.submit(post_in_connection, "second", "+82 10 0000 0000")
                self.assertTrue(second_started.wait(10))
                blocked = False
                deadline = monotonic() + 10
                while monotonic() < deadline:
                    with connection.cursor() as cursor:
                        cursor.execute("SELECT pg_blocking_pids(%s)", [pids["second"]])
                        blocked = pids["first"] in cursor.fetchone()[0]
                    if blocked or second.done():
                        break
                    sleep(0.02)
                self.assertTrue(blocked, "The second request must wait before checking the tenant budget")
            finally:
                release_first.set()
            self.assertEqual(first.result(timeout=10), 201)
            self.assertEqual(second.result(timeout=10), 429)
        self.assertEqual(calls, [False, True])
        self.assertEqual(LandingConsultRequest.objects.filter(tenant=tenant).count(), 5)
