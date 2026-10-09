"""Current tenant roles must control messaging settings and manual-send access."""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken

from apps.core.models import Tenant, TenantMembership
from apps.domains.messaging.models import ScheduledNotification


@override_settings(
    ALLOWED_HOSTS=["api.hakwonplus.com", "testserver"],
    TENANT_HEADER_CODE_ALLOWED_HOSTS=("api.hakwonplus.com",),
)
class MessagingCurrentRoleBoundaryTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(code="qa-messaging-roles", name="Messaging QA", messaging_is_active=True)
        cls.other = Tenant.objects.create(code="qa-messaging-other", name="Other QA", messaging_is_active=True)
        cls.users = {}
        for role in ("owner", "admin", "staff", "teacher"):
            user = get_user_model().objects.create_user(
                username=f"qa-messaging-{role}", tenant=cls.tenant,
                is_staff=True, is_superuser=True, must_change_password=False,
            )
            TenantMembership.ensure_active(tenant=cls.tenant, user=user, role=role)
            cls.users[role] = user

    def setUp(self):
        for name in ("resolve_kakao_channel", "get_tenant_channel_status"):
            mocked = patch(f"apps.domains.messaging.views.info_views.{name}", return_value={})
            mocked.start()
            self.addCleanup(mocked.stop)

    def client_for(self, role, *, tenant=None):
        user = self.users[role]
        tenant = tenant or self.tenant
        token = AccessToken.for_user(user)
        token["tenant_id"] = tenant.id
        token["token_version"] = user.token_version or 0
        client = APIClient(HTTP_HOST="api.hakwonplus.com", HTTP_X_TENANT_CODE=tenant.code)
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
        return client

    def test_teacher_and_staff_global_flags_do_not_offer_settings_management(self):
        for role in ("teacher", "staff"):
            with self.subTest(role=role):
                response = self.client_for(role).get("/api/v1/messaging/info/")
                self.assertEqual(response.status_code, 200, response.content)
                self.assertFalse(response.json()["can_manage_messaging"])

    def test_teacher_and_staff_cannot_disable_academy_messaging(self):
        for role in ("teacher", "staff"):
            with self.subTest(role=role):
                response = self.client_for(role).patch(
                    "/api/v1/messaging/info/", {"tenant_messaging_enabled": False}, format="json",
                )
                self.assertEqual(response.status_code, 403, response.content)
                self.tenant.refresh_from_db()
                self.assertTrue(self.tenant.messaging_is_active)
        self.assertFalse(ScheduledNotification.objects.exists())

    def test_teacher_and_staff_cannot_enter_auto_send_write(self):
        for role in ("teacher", "staff"):
            with self.subTest(role=role):
                response = self.client_for(role).patch("/api/v1/messaging/auto-send/", {"configs": []}, format="json")
                self.assertEqual(response.status_code, 403, response.content)

    def test_owner_admin_can_change_settings_and_read_back_without_global_flags(self):
        for role in ("owner", "admin"):
            self.users[role].is_staff = False
            self.users[role].is_superuser = False
            self.users[role].save(update_fields=["is_staff", "is_superuser"])
            client = self.client_for(role)
            for enabled in (False, True):
                with self.subTest(role=role, enabled=enabled):
                    response = client.patch("/api/v1/messaging/info/", {"tenant_messaging_enabled": enabled}, format="json")
                    self.assertEqual(response.status_code, 200, response.content)
                    reloaded = client.get("/api/v1/messaging/info/")
                    self.assertEqual(reloaded.status_code, 200)
                    self.assertTrue(reloaded.json()["can_manage_messaging"])
                    self.assertEqual(reloaded.json()["tenant_messaging_enabled"], enabled)
                    self.tenant.refresh_from_db()
                    self.assertEqual(self.tenant.messaging_is_active, enabled)
        self.assertFalse(ScheduledNotification.objects.exists())

    @patch("apps.domains.messaging.views.operations_views.build_send_preflight", return_value={"ok": True})
    def test_general_staff_cannot_use_manual_send_preflight(self, build):
        response = self.client_for("staff").post("/api/v1/messaging/send/preflight/", {
            "student_ids": [1], "send_to": "parent", "raw_body": "QA message", "block_category": "notice",
        }, format="json")
        self.assertEqual(response.status_code, 403, response.content)
        build.assert_not_called()
        self.assertFalse(ScheduledNotification.objects.exists())

    @patch("apps.domains.messaging.views.operations_views.build_send_preflight", return_value={"ok": True})
    def test_owner_admin_teacher_retain_preflight_without_global_flags(self, build):
        for role in ("owner", "admin", "teacher"):
            with self.subTest(role=role):
                self.users[role].is_staff = False
                self.users[role].is_superuser = False
                self.users[role].save(update_fields=["is_staff", "is_superuser"])
                response = self.client_for(role).post("/api/v1/messaging/send/preflight/", {
                    "student_ids": [1], "send_to": "parent", "raw_body": "QA message", "block_category": "notice",
                }, format="json")
                self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(build.call_count, 3)
        self.assertFalse(ScheduledNotification.objects.exists())

    def test_revoked_and_foreign_membership_cannot_change_settings(self):
        client = self.client_for("owner")
        TenantMembership.objects.filter(tenant=self.tenant, user=self.users["owner"]).update(is_active=False)
        for blocked in (client, self.client_for("admin", tenant=self.other)):
            with self.subTest():
                response = blocked.patch("/api/v1/messaging/info/", {"tenant_messaging_enabled": False}, format="json")
                self.assertEqual(response.status_code, 401, response.content)
        self.tenant.refresh_from_db()
        self.other.refresh_from_db()
        self.assertTrue(self.tenant.messaging_is_active)
        self.assertTrue(self.other.messaging_is_active)
