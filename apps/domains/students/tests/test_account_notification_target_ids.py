from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from apps.domains.students.services.account_notifications import (
    _parent_target_id,
    send_parent_password_changed_notice,
)


class AccountNotificationTargetIdTests(SimpleTestCase):
    def test_parent_target_uses_student_identity_without_phone(self):
        student = SimpleNamespace(id=17, ps_number="S017")

        target_id = _parent_target_id(student)

        self.assertEqual(target_id, "parent:17")
        self.assertNotIn("010", target_id)

    @patch(
        "apps.domains.students.services.account_notifications._send_owner_account_notice",
        return_value=True,
    )
    def test_unlinked_parent_target_uses_parent_identity_without_phone(self, send_notice):
        parent = SimpleNamespace(
            id=23,
            tenant_id=5,
            phone="01012345678",
            name="학부모",
            user=SimpleNamespace(username="parent-user"),
            students=MagicMock(),
        )
        parent.students.filter.return_value.order_by.return_value.first.return_value = None

        sent = send_parent_password_changed_notice(
            parent=parent,
            password="temporary-secret",
        )

        self.assertTrue(sent)
        target_id = send_notice.call_args.kwargs["log_target_id"]
        self.assertEqual(target_id, "parent-account:23")
        self.assertNotIn(parent.phone, target_id)
