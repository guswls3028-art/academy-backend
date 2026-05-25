from unittest import TestCase


class MessagingPolicyTests(TestCase):
    """자동발송 정책/구현상태 회귀 테스트."""

    def test_manual_only_triggers_are_not_default_enabled(self):
        from apps.domains.messaging.policy import (
            get_trigger_implementation_status,
            is_auto_send_enabled_by_default,
        )

        self.assertEqual(
            get_trigger_implementation_status("lecture_session_reminder"),
            "manual_only",
        )
        self.assertFalse(is_auto_send_enabled_by_default("lecture_session_reminder"))

    def test_unscheduled_assignment_batch_is_manual_only(self):
        from apps.domains.messaging.policy import (
            get_trigger_implementation_status,
            is_auto_send_enabled_by_default,
        )

        self.assertEqual(
            get_trigger_implementation_status("assignment_not_submitted"),
            "manual_only",
        )
        self.assertFalse(is_auto_send_enabled_by_default("assignment_not_submitted"))

    def test_clinic_reminder_is_default_enabled_after_scheduler_wiring(self):
        from apps.domains.messaging.policy import (
            get_trigger_implementation_status,
            is_auto_send_enabled_by_default,
        )

        self.assertEqual(get_trigger_implementation_status("clinic_reminder"), "implemented")
        self.assertTrue(is_auto_send_enabled_by_default("clinic_reminder"))
