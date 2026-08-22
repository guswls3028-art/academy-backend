from __future__ import annotations

from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.utils import timezone

from apps.core.models import Tenant
from apps.domains.messaging.models import ScheduledNotification
from apps.domains.parents.models import Parent
from apps.domains.students.models import Student


User = get_user_model()


class RepairFailedFirstEnrollmentNoticesTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(
            name="복구학원",
            code="notice-recovery",
            is_active=True,
        )
        self.student_user = User.objects.create_user(
            username="notice-recovery-student",
            password="old-student-password",
            tenant=self.tenant,
            phone="01011112222",
        )
        self.parent_user = User.objects.create_user(
            username="notice-recovery-parent",
            password="old-parent-password",
            tenant=self.tenant,
            phone="01033334444",
        )
        self.parent = Parent.objects.create(
            tenant=self.tenant,
            user=self.parent_user,
            name="복구학부모",
            phone="01033334444",
        )
        self.student = Student.objects.create(
            tenant=self.tenant,
            user=self.student_user,
            parent=self.parent,
            ps_number="RECOVERY-1",
            omr_code="11112222",
            name="복구학생",
            phone="01011112222",
            parent_phone="01033334444",
        )
        self.student_outbox = self._failed_outbox(
            trigger="registration_approved_student",
            target_id=f"student:{self.student.id}",
        )
        self.parent_outbox = self._failed_outbox(
            trigger="registration_approved_parent",
            target_id=f"parent:{self.student.id}",
        )

    def _failed_outbox(self, *, trigger: str, target_id: str):
        return ScheduledNotification.objects.create(
            tenant=self.tenant,
            trigger=trigger,
            send_at=timezone.now(),
            payload={
                "target_id": target_id,
                "event_type": trigger,
                "redacted": True,
            },
            origin_type="excel_import",
            origin_id="excel-job-1",
            status=ScheduledNotification.Status.FAILED,
            error_message="business_tenant_messaging_disabled",
        )

    def _command_args(self):
        return (
            "repair_failed_first_enrollment_notices",
            "--tenant-id",
            str(self.tenant.id),
            "--student-ids",
            str(self.student.id),
        )

    def test_dry_run_does_not_change_passwords_or_create_outboxes(self):
        output = StringIO()

        call_command(*self._command_args(), stdout=output)

        self.student_user.refresh_from_db()
        self.parent_user.refresh_from_db()
        self.student.refresh_from_db()
        self.assertTrue(self.student_user.check_password("old-student-password"))
        self.assertTrue(self.parent_user.check_password("old-parent-password"))
        self.assertEqual(ScheduledNotification.objects.count(), 2)
        self.assertEqual(
            self.student.pending_account_notice_student_password_ciphertext,
            "",
        )
        self.assertIn("mode=dry-run", output.getvalue())

    @patch(
        "apps.core.management.commands.repair_failed_first_enrollment_notices.generate_temp_password",
        side_effect=["111111", "222222"],
    )
    @patch(
        "apps.core.management.commands.repair_failed_first_enrollment_notices.dispatch_pending_account_notice",
        return_value={"status": "enqueued", "enqueued": 2},
    )
    def test_apply_rotates_never_used_accounts_and_stages_replacement_pair(
        self,
        dispatch_mock,
        _password_mock,
    ):
        output = StringIO()

        call_command(
            *self._command_args(),
            "--apply",
            "--confirm-tenant",
            self.tenant.code,
            stdout=output,
        )

        self.student_user.refresh_from_db()
        self.parent_user.refresh_from_db()
        self.student.refresh_from_db()
        self.assertTrue(self.student_user.check_password("111111"))
        self.assertTrue(self.parent_user.check_password("222222"))
        self.assertTrue(self.student_user.must_change_password)
        self.assertTrue(self.parent_user.must_change_password)
        self.assertNotEqual(
            self.student.pending_account_notice_student_password_ciphertext,
            "",
        )
        self.assertNotEqual(
            self.student.pending_account_notice_parent_password_ciphertext,
            "",
        )
        dispatch_mock.assert_called_once_with(student_id=self.student.id)
        self.assertIn("credentials_rotated=2", output.getvalue())

    def test_apply_refuses_an_account_that_has_logged_in(self):
        self.student_user.last_login = timezone.now()
        self.student_user.save(update_fields=["last_login"])

        with self.assertRaisesMessage(CommandError, "account_already_used"):
            call_command(
                *self._command_args(),
                "--apply",
                "--confirm-tenant",
                self.tenant.code,
            )

    def test_dry_run_refuses_missing_exact_parent_failure(self):
        self.parent_outbox.delete()

        with self.assertRaisesMessage(CommandError, "exact_failed_outbox_pair_required"):
            call_command(*self._command_args())

    def test_cross_tenant_student_id_is_not_selected(self):
        other_tenant = Tenant.objects.create(
            name="다른학원",
            code="notice-recovery-other",
            is_active=True,
        )

        with self.assertRaisesMessage(CommandError, "active_students_not_found"):
            call_command(
                "repair_failed_first_enrollment_notices",
                "--tenant-id",
                str(other_tenant.id),
                "--student-ids",
                str(self.student.id),
            )
