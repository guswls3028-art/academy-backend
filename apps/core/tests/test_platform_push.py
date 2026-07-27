from unittest.mock import patch

from django.test import TestCase

from apps.core.models import PlatformPushOutbox
from apps.core.services.platform_push import (
    build_platform_inbox_payload,
    dispatch_platform_push_batch,
    enqueue_platform_inbox,
)


class PlatformInboxPushTests(TestCase):
    def test_enqueue_is_deduplicated_and_payload_contains_no_private_content(self):
        self.assertTrue(enqueue_platform_inbox(kind="bug", item_id=42))
        self.assertFalse(enqueue_platform_inbox(kind="bug", item_id=42))
        self.assertEqual(PlatformPushOutbox.objects.count(), 1)

        payload = build_platform_inbox_payload(kind="bug", count=1)
        self.assertEqual(payload["url"], "/dev/inbox")
        self.assertEqual(payload["tag"], "platform-inbox-bug")
        self.assertNotIn("name", payload)
        self.assertNotIn("phone", payload)
        self.assertNotIn("content", payload)

    @patch("apps.domains.teacher_app.push.service.send_push_to_platform_admins")
    def test_dispatch_collapses_same_kind_and_marks_items_sent(self, send_push):
        enqueue_platform_inbox(kind="contact", item_id=1)
        enqueue_platform_inbox(kind="contact", item_id=2)

        result = dispatch_platform_push_batch()

        self.assertEqual(result["sent"], 2)
        self.assertEqual(result["notifications"], 1)
        self.assertEqual(
            PlatformPushOutbox.objects.filter(
                status=PlatformPushOutbox.Status.SENT
            ).count(),
            2,
        )
        payload = send_push.call_args.args[0]
        self.assertIn("2건", payload["body"])

    @patch(
        "apps.domains.teacher_app.push.service.send_push_to_platform_admins",
        side_effect=RuntimeError("temporary"),
    )
    def test_transient_failure_is_retried_without_losing_item(self, _send_push):
        enqueue_platform_inbox(kind="feedback", item_id=7)

        result = dispatch_platform_push_batch()

        row = PlatformPushOutbox.objects.get()
        self.assertEqual(result["retry"], 1)
        self.assertEqual(row.status, PlatformPushOutbox.Status.PENDING)
        self.assertEqual(row.attempts, 1)
        self.assertEqual(row.last_error, "RuntimeError")
