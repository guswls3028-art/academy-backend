from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.models import Tenant, TenantMembership
from apps.domains.teacher_app.push.models import PushNotificationConfig
from apps.domains.teacher_app.push.views import PushNotificationConfigView


class PushNotificationConfigSafeGetTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.tenant = Tenant.objects.create(name="Push Config", code="push-config")
        self.user = get_user_model().objects.create_user(
            username="push-config-owner",
            password="test1234",
            tenant=self.tenant,
            is_staff=True,
        )
        TenantMembership.ensure_active(tenant=self.tenant, user=self.user, role="owner")

    def _request(self, method: str, data=None):
        request = getattr(self.factory, method)("/teacher/push/config/", data or {}, format="json")
        request.tenant = self.tenant
        force_authenticate(request, user=self.user)
        return request

    def test_get_returns_model_defaults_without_creating_row(self):
        response = PushNotificationConfigView.as_view()(self._request("get"))

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(
            response.data,
            {
                "student_registration": True,
                "qna_new_question": True,
                "exam_submission": True,
                "clinic_booking": False,
                "video_encoding_complete": True,
            },
        )
        self.assertFalse(PushNotificationConfig.objects.exists())

    def test_patch_is_the_explicit_creation_boundary(self):
        response = PushNotificationConfigView.as_view()(self._request(
            "patch",
            {"clinic_booking": True},
        ))

        self.assertEqual(response.status_code, 200, response.data)
        config = PushNotificationConfig.objects.get(user=self.user, tenant=self.tenant)
        self.assertTrue(config.clinic_booking)
