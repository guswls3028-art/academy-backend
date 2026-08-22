import os
from unittest.mock import MagicMock, patch

from django.test import TestCase

from apps.core.models import Tenant


class DisabledTenantMessagingPolicyTests(TestCase):
    def setUp(self):
        self.disabled_tenant = Tenant.objects.create(
            id=4,
            code="ymath-disabled",
            name="Ymath",
            is_active=True,
            messaging_is_active=False,
        )
        self.enabled_tenant = Tenant.objects.create(
            id=11,
            code="enabled-messaging",
            name="Enabled Messaging",
            is_active=True,
            messaging_is_active=True,
        )

    def test_tenant_owner_setting_disables_all_messaging(self):
        from apps.domains.messaging.policy import (
            get_messaging_disabled_reason,
            is_messaging_disabled,
        )

        self.assertTrue(is_messaging_disabled(4))
        self.assertIn("대표 또는 관리자", get_messaging_disabled_reason(4))

    def test_enabled_tenant_is_not_hidden_in_code(self):
        from apps.domains.messaging.policy import is_messaging_disabled

        self.assertFalse(is_messaging_disabled(11))

    def test_owner_customer_setting_is_not_a_global_runtime_hold(self):
        from apps.domains.messaging.policy import is_messaging_runtime_held

        self.assertFalse(is_messaging_runtime_held(4))

    @patch.dict(os.environ, {"MESSAGING_DISABLED_TENANT_IDS": "11"})
    def test_emergency_ops_hold_is_separate_from_tenant_setting(self):
        from apps.domains.messaging.policy import (
            get_messaging_disabled_reason,
            is_messaging_disabled,
            is_messaging_ops_held,
        )

        self.assertTrue(is_messaging_disabled(11))
        self.assertTrue(is_messaging_ops_held(11))
        self.assertIn("긴급 장애", get_messaging_disabled_reason(11))

    @patch("apps.domains.messaging.sqs_queue.MessagingSQSQueue")
    def test_disabled_source_tenant_skipped_before_sqs(self, mock_queue_cls):
        from apps.domains.messaging.services import enqueue_alimtalk

        mock_queue_cls.return_value = MagicMock()

        result = enqueue_alimtalk(
            tenant_id=1,
            source_tenant_id=4,
            trusted_business_tenant_id=4,
            to="01012345678",
            text="test",
            message_mode="alimtalk",
            event_type="registration_approved_parent",
        )

        self.assertFalse(result)
        mock_queue_cls.assert_not_called()
