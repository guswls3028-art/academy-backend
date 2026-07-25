from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.core.models import LandingConsultRequest, Tenant


@override_settings(
    ALLOWED_HOSTS=["api.hakwonplus.com", "testserver"],
    TENANT_HEADER_CODE_ALLOWED_HOSTS=("api.hakwonplus.com",),
)
class LandingConsultPrivacyConsentTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.tenant = Tenant.objects.create(name="Hakwonplus", code="hakwonplus")
        self.url = "/api/v1/core/landing/consult/"

    def post(self, **overrides):
        payload = {
            "name": "홍길동",
            "phone": "010-0000-0000",
            "interest": "매치업·PPT",
            "message": "데모 요청",
            "source": "promo-demo",
            "privacy_agreed": True,
            "privacy_policy_version": "1.2",
            **overrides,
        }
        return self.client.post(
            self.url,
            payload,
            format="json",
            HTTP_HOST="api.hakwonplus.com",
            HTTP_X_TENANT_CODE=self.tenant.code,
        )

    def test_promo_request_records_server_timestamp_and_policy_version(self):
        response = self.post()

        self.assertEqual(response.status_code, 201)
        consult = LandingConsultRequest.objects.get(id=response.json()["id"])
        self.assertTrue(consult.privacy_agreed)
        self.assertEqual(consult.privacy_policy_version, "1.2")
        self.assertIsNotNone(consult.privacy_agreed_at)

    def test_promo_request_without_explicit_consent_is_rejected(self):
        response = self.post(privacy_agreed=False)

        self.assertEqual(response.status_code, 400)
        self.assertIn("개인정보 수집·이용 동의 정보를", response.json()["detail"][0])
        self.assertFalse(LandingConsultRequest.objects.exists())

    def test_promo_request_with_stale_policy_version_is_rejected(self):
        response = self.post(privacy_policy_version="1.1")

        self.assertEqual(response.status_code, 400)
        self.assertFalse(LandingConsultRequest.objects.exists())

    def test_existing_non_promo_landing_contract_remains_compatible(self):
        response = self.post(
            source="landing",
            privacy_agreed=False,
            privacy_policy_version="",
        )

        self.assertEqual(response.status_code, 201)
        consult = LandingConsultRequest.objects.get(id=response.json()["id"])
        self.assertFalse(consult.privacy_agreed)
        self.assertEqual(consult.privacy_policy_version, "")
        self.assertIsNone(consult.privacy_agreed_at)
