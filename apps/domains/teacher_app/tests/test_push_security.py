from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.models import Tenant, TenantMembership
from apps.domains.teacher_app.push.models import PushSubscription
from apps.domains.teacher_app.push.serializers import PushSubscribeSerializer
from apps.domains.teacher_app.push.service import send_push_to_platform_admins
from apps.domains.teacher_app.push.views import (
    PlatformPushSubscribeView,
    PushSubscribeView,
)

User = get_user_model()


class PushEndpointValidationTests(TestCase):
    def test_rejects_private_or_arbitrary_https_endpoint(self):
        for endpoint in (
            "http://fcm.googleapis.com/fcm/send/x",
            "https://127.0.0.1/push",
            "https://example.com/push",
        ):
            serializer = PushSubscribeSerializer(data={
                "endpoint": endpoint,
                "p256dh_key": "p" * 32,
                "auth_key": "a" * 16,
            })
            self.assertFalse(serializer.is_valid(), endpoint)

    def test_accepts_apple_google_and_mozilla_push_services(self):
        for endpoint in (
            "https://web.push.apple.com/QD123",
            "https://fcm.googleapis.com/fcm/send/abc",
            "https://updates.push.services.mozilla.com/wpush/v2/abc",
        ):
            serializer = PushSubscribeSerializer(data={
                "endpoint": endpoint,
                "p256dh_key": "p" * 32,
                "auth_key": "a" * 16,
            })
            self.assertTrue(serializer.is_valid(), serializer.errors)


class PlatformPushPermissionTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.tenant = Tenant.objects.create(
            name="Platform",
            code="platform_push_owner",
            is_active=True,
        )
        self.owner = User.objects.create_user(
            username="platform_push_owner",
            password="pw1234",
            tenant=self.tenant,
            name="Owner",
        )
        self.teacher = User.objects.create_user(
            username="platform_push_teacher",
            password="pw1234",
            tenant=self.tenant,
            name="Teacher",
        )
        TenantMembership.ensure_active(
            tenant=self.tenant,
            user=self.owner,
            role="owner",
        )
        TenantMembership.ensure_active(
            tenant=self.tenant,
            user=self.teacher,
            role="teacher",
        )
        self.payload = {
            "endpoint": "https://web.push.apple.com/QD123",
            "p256dh_key": "p" * 32,
            "auth_key": "a" * 16,
        }

    def _post(self, view, user):
        request = self.factory.post("/push/", self.payload, format="json")
        request.tenant = self.tenant
        force_authenticate(request, user=user)
        return view.as_view()(request)

    def test_platform_subscription_requires_owner_and_sets_platform_scope(self):
        with override_settings(OWNER_TENANT_ID=self.tenant.id):
            denied = self._post(PlatformPushSubscribeView, self.teacher)
            allowed = self._post(PlatformPushSubscribeView, self.owner)

        self.assertEqual(denied.status_code, 403)
        self.assertEqual(allowed.status_code, 201)
        self.assertEqual(
            PushSubscription.objects.get(user=self.owner).app_scope,
            PushSubscription.AppScope.PLATFORM,
        )

    def test_teacher_subscription_cannot_become_platform_subscription(self):
        response = self._post(PushSubscribeView, self.teacher)

        self.assertEqual(response.status_code, 201)
        self.assertEqual(
            PushSubscription.objects.get(user=self.teacher).app_scope,
            PushSubscription.AppScope.TEACHER,
        )

    @patch("apps.domains.teacher_app.push.service._deliver", return_value=True)
    def test_platform_delivery_excludes_non_owner_and_inactive_membership(self, deliver):
        inactive_owner = User.objects.create_user(
            username="platform_push_inactive",
            password="pw1234",
            tenant=self.tenant,
            name="Inactive",
        )
        membership = TenantMembership.ensure_active(
            tenant=self.tenant,
            user=inactive_owner,
            role="owner",
        )
        membership.is_active = False
        membership.save(update_fields=["is_active"])
        for user in (self.owner, self.teacher, inactive_owner):
            PushSubscription.objects.create(
                tenant=self.tenant,
                user=user,
                endpoint=f"https://web.push.apple.com/{user.id}",
                p256dh_key="p",
                auth_key="a",
                app_scope=PushSubscription.AppScope.PLATFORM,
            )

        with override_settings(OWNER_TENANT_ID=self.tenant.id):
            sent = send_push_to_platform_admins({"title": "test"})

        self.assertEqual(sent, 1)
        self.assertEqual(deliver.call_count, 1)
