from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase


class DisabledTenantMessagingPolicyTests(SimpleTestCase):
    def test_ymath_is_temporarily_disabled_for_all_messaging(self):
        from apps.domains.messaging.policy import is_messaging_disabled

        self.assertTrue(is_messaging_disabled(4))

    @patch("apps.domains.messaging.sqs_queue.MessagingSQSQueue")
    def test_disabled_source_tenant_skipped_before_sqs(self, mock_queue_cls):
        from apps.domains.messaging.services import enqueue_sms

        mock_queue_cls.return_value = MagicMock()

        result = enqueue_sms(
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
