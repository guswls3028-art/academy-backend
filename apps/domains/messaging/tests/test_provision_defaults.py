from __future__ import annotations

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.models import Tenant, TenantMembership
from apps.domains.messaging.default_templates import get_default_templates
from apps.domains.messaging.models import AutoSendConfig, MessageTemplate
from apps.domains.messaging.views.config_views import (
    AutoSendConfigView,
    ProvisionDefaultTemplatesView,
)


User = get_user_model()


class ProvisionDefaultTemplatesTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.tenant = Tenant.objects.create(
            code="msg-provision",
            name="Msg Provision",
            is_active=True,
        )
        self.user = User.objects.create_user(
            username="msg-provision-owner",
            password="test1234",
            tenant=self.tenant,
        )
        TenantMembership.ensure_active(tenant=self.tenant, user=self.user, role="owner")

    def _request(self, method: str, path: str, data=None):
        request = getattr(self.factory, method)(path, data=data or {}, format="json")
        force_authenticate(request, user=self.user)
        request.user = self.user
        request.tenant = self.tenant
        return request

    def test_existing_template_body_and_subject_are_not_overwritten(self):
        defaults = get_default_templates(self.tenant.name)
        default = defaults["registration_approved_student"]
        template = MessageTemplate.objects.create(
            tenant=self.tenant,
            name=default["name"],
            category=default["category"],
            subject="원장님이 바꾼 제목",
            body="원장님이 바꾼 본문",
            is_system=True,
        )

        response = ProvisionDefaultTemplatesView.as_view()(
            self._request("post", "/api/v1/messaging/provision-defaults/")
        )

        self.assertEqual(response.status_code, 200)
        template.refresh_from_db()
        self.assertEqual(template.subject, "원장님이 바꾼 제목")
        self.assertEqual(template.body, "원장님이 바꾼 본문")

    def test_autosend_patch_enabled_only_preserves_template_and_timing(self):
        template = MessageTemplate.objects.create(
            tenant=self.tenant,
            name="출결 안내",
            category="attendance",
            subject="",
            body="본문",
            is_system=True,
        )
        config = AutoSendConfig.objects.create(
            tenant=self.tenant,
            trigger="check_in_complete",
            template=template,
            enabled=True,
            message_mode="alimtalk",
            minutes_before=30,
        )

        response = AutoSendConfigView.as_view()(
            self._request(
                "patch",
                "/api/v1/messaging/auto-send/",
                {"configs": [{"trigger": "check_in_complete", "enabled": False}]},
            )
        )

        self.assertEqual(response.status_code, 200)
        config.refresh_from_db()
        self.assertFalse(config.enabled)
        self.assertEqual(config.template_id, template.id)
        self.assertEqual(config.minutes_before, 30)

    def test_autosend_patch_parses_string_false_without_enabling(self):
        config = AutoSendConfig.objects.create(
            tenant=self.tenant,
            trigger="check_in_complete",
            enabled=True,
            message_mode="alimtalk",
        )

        response = AutoSendConfigView.as_view()(
            self._request(
                "patch",
                "/api/v1/messaging/auto-send/",
                {"configs": [{"trigger": "check_in_complete", "enabled": "false"}]},
            )
        )

        self.assertEqual(response.status_code, 200)
        config.refresh_from_db()
        self.assertFalse(config.enabled)

    def test_community_answer_triggers_are_not_enabled_by_default(self):
        response = AutoSendConfigView.as_view()(
            self._request("get", "/api/v1/messaging/auto-send/")
        )

        self.assertEqual(response.status_code, 200)
        by_trigger = {item["trigger"]: item for item in response.data}
        self.assertFalse(by_trigger["qna_answered"]["enabled"])
        self.assertFalse(by_trigger["counsel_answered"]["enabled"])
        self.assertFalse(
            AutoSendConfig.objects.get(
                tenant=self.tenant,
                trigger="qna_answered",
            ).enabled
        )
        self.assertFalse(
            AutoSendConfig.objects.get(
                tenant=self.tenant,
                trigger="counsel_answered",
            ).enabled
        )

    def test_provision_defaults_does_not_auto_submit_kakao_template_review(self):
        self.tenant.kakao_pfid = "KA01PF"
        self.tenant.own_solapi_api_key = "key"
        self.tenant.own_solapi_api_secret = "secret"
        self.tenant.save(
            update_fields=["kakao_pfid", "own_solapi_api_key", "own_solapi_api_secret"]
        )

        with patch(
            "apps.domains.messaging.solapi_template_client.create_kakao_template"
        ) as mocked_create:
            response = ProvisionDefaultTemplatesView.as_view()(
                self._request("post", "/api/v1/messaging/provision-defaults/")
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["submitted_reviews"], 0)
        self.assertEqual(response.data["review_note"], "")
        mocked_create.assert_not_called()
