from __future__ import annotations

from collections import Counter
from io import StringIO
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.core.models import Tenant
from apps.domains.enrollment.models import Enrollment
from apps.domains.lectures.models import Lecture
from apps.domains.messaging.models import (
    AutoSendConfig,
    MessageTemplate,
    NotificationLog,
    ScheduledNotification,
)
from apps.domains.parents.models import Parent
from apps.domains.students.models import Student


User = get_user_model()
COMMAND_MODULE = "apps.core.management.commands.repair_failed_first_enrollment_notices"
TARGET_IDS = (3656, 4102, 4103, 4104, 4105)


@override_settings(
    OWNER_TENANT_ID=1,
    TEST_TENANT_ID=9999,
    MESSAGING_PROVIDER_LOW_BALANCE_ALERT_THRESHOLD=10_000,
    MESSAGING_TENANT_BINDING_KEY="test-credential-recovery-binding",
)
class RepairFailedFirstEnrollmentNoticesTests(TestCase):
    def setUp(self):
        self.owner = Tenant.objects.create(
            id=1,
            name="공용 알림톡 owner",
            code="owner",
            is_active=True,
            messaging_is_active=True,
        )
        self.tenant = Tenant.objects.create(
            id=11,
            name="복구학원",
            code="notice-recovery",
            is_active=True,
            messaging_is_active=True,
        )
        self.lecture = Lecture.objects.create(
            tenant=self.tenant,
            title="복구 검증 강의",
            name="복구 검증 강의",
            subject="수학",
        )
        self._create_templates()

        self.solapi_client = MagicMock()
        self.solapi_client.get_balance.return_value = SimpleNamespace(balance="42911.1")
        self.solapi_patcher = patch(
            f"{COMMAND_MODULE}.get_solapi_client",
            return_value=self.solapi_client,
        )
        self.solapi_patcher.start()
        self.addCleanup(self.solapi_patcher.stop)

        self.queue_client = MagicMock()
        self.queue_client.get_queue_counts.return_value = {
            "visible": 0,
            "not_visible": 0,
            "delayed": 0,
        }
        self.queue_patcher = patch(
            f"{COMMAND_MODULE}.get_queue_client",
            return_value=self.queue_client,
        )
        self.queue_patcher.start()
        self.addCleanup(self.queue_patcher.stop)

        self.students: dict[int, Student] = {}
        self.original_password_hashes: dict[int, str] = {}
        for student_id in TARGET_IDS:
            self.students[student_id] = self._create_student(student_id)

        self.shared_sibling = self._create_student(
            5001,
            parent=self.students[3656].parent,
        )
        self._student_only_history(self.students[3656])
        for student_id in (4102, 4103, 4104, 4105):
            self._failed_pair(self.students[student_id])

    def _create_templates(self):
        for trigger, template_id in (
            ("registration_approved_student", "provider-student"),
            ("registration_approved_parent", "provider-parent"),
        ):
            template = MessageTemplate.objects.create(
                tenant=self.owner,
                category=MessageTemplate.Category.SIGNUP,
                name=trigger,
                body=(
                    "#{학생이름} #{학생아이디} #{학생비밀번호} "
                    "#{학부모아이디} #{학부모비밀번호} #{사이트링크} #{비밀번호안내}"
                ),
                solapi_template_id=template_id,
                solapi_status="APPROVED",
                is_system=True,
            )
            AutoSendConfig.objects.create(
                tenant=self.owner,
                trigger=trigger,
                template=template,
                enabled=False,
                message_mode="alimtalk",
            )

    def _create_student(self, student_id: int, *, parent: Parent | None = None) -> Student:
        student_phone = f"010{student_id:08d}"
        if parent is None:
            parent_phone = f"011{student_id:08d}"
            parent_user = User.objects.create_user(
                username=f"parent-{student_id}",
                password=f"old-parent-{student_id}",
                tenant=self.tenant,
                phone=parent_phone,
                token_version=2,
                is_active=True,
            )
            parent = Parent.objects.create(
                tenant=self.tenant,
                user=parent_user,
                name=f"학부모-{student_id}",
                phone=parent_phone,
            )
            self.original_password_hashes[parent_user.id] = parent_user.password
        else:
            parent_phone = parent.phone

        student_user = User.objects.create_user(
            username=f"student-{student_id}",
            password=f"old-student-{student_id}",
            tenant=self.tenant,
            phone=student_phone,
            token_version=2,
            is_active=True,
        )
        self.original_password_hashes[student_user.id] = student_user.password
        student = Student.objects.create(
            id=student_id,
            tenant=self.tenant,
            user=student_user,
            parent=parent,
            ps_number=f"RECOVERY-{student_id}",
            omr_code=f"{student_id:08d}"[-8:],
            name=f"학생-{student_id}",
            phone=student_phone,
            parent_phone=parent_phone,
        )
        Enrollment.objects.create(
            tenant=self.tenant,
            student=student,
            lecture=self.lecture,
            status="ACTIVE",
        )
        return student

    def _outbox(
        self,
        *,
        student: Student,
        trigger: str,
        status: str,
        error_message: str = "",
    ) -> ScheduledNotification:
        prefix = "student" if trigger.endswith("student") else "parent"
        return ScheduledNotification.objects.create(
            tenant=self.tenant,
            trigger=trigger,
            send_at=timezone.now(),
            payload={
                "target_id": f"{prefix}:{student.id}",
                "event_type": trigger,
                "message_mode": "alimtalk",
                "redacted": True,
            },
            origin_type="excel_import",
            origin_id=f"excel-job-{student.id}",
            status=status,
            error_message=error_message,
        )

    def _failed_pair(self, student: Student):
        self._outbox(
            student=student,
            trigger="registration_approved_student",
            status=ScheduledNotification.Status.FAILED,
            error_message="business_tenant_messaging_disabled",
        )
        self._outbox(
            student=student,
            trigger="registration_approved_parent",
            status=ScheduledNotification.Status.FAILED,
            error_message="business_tenant_messaging_disabled",
        )

    def _student_only_history(self, student: Student):
        self._outbox(
            student=student,
            trigger="registration_approved_student",
            status=ScheduledNotification.Status.SENT,
        )
        self._outbox(
            student=student,
            trigger="registration_approved_parent",
            status=ScheduledNotification.Status.SENT,
        )
        NotificationLog.objects.create(
            tenant=self.owner,
            source_tenant=self.tenant,
            success=False,
            status="ambiguous",
            message_mode="alimtalk",
            notification_type="registration_approved_student",
            target_type="account",
            target_id=f"student:{student.id}",
            provider_message_id="",
            failure_reason="provider_result_unresolved",
        )
        NotificationLog.objects.create(
            tenant=self.owner,
            source_tenant=self.tenant,
            success=True,
            status="sent",
            message_mode="alimtalk",
            notification_type="registration_approved_parent",
            target_type="account",
            target_id=f"parent:{student.id}",
            provider_message_id="provider-parent-proof",
            business_idempotency_key="parent-provider-proof-key",
        )

    def _command_args(self):
        return (
            "repair_failed_first_enrollment_notices",
            "--tenant-id",
            str(self.tenant.id),
            "--student-ids",
            ",".join(str(student_id) for student_id in TARGET_IDS),
        )

    def _call_apply(self, *, stdout=None):
        return call_command(
            *self._command_args(),
            "--apply",
            "--confirm-tenant",
            self.tenant.code,
            stdout=stdout,
        )

    def test_dry_run_is_secret_free_and_does_not_mutate(self):
        output = StringIO()
        outbox_count = ScheduledNotification.objects.count()
        hashes_before = dict(self.original_password_hashes)

        call_command(*self._command_args(), stdout=output)

        text = output.getvalue()
        self.assertIn("mode=dry-run", text)
        self.assertIn("expected_credentials_rotated=9", text)
        self.assertIn("expected_outboxes=9", text)
        self.assertIn("secrets=redacted", text)
        for secret in (
            "old-student-3656",
            "old-parent-4102",
            self.students[3656].phone,
            self.students[3656].parent_phone,
            self.students[3656].user.username,
        ):
            self.assertNotIn(secret, text)
        self.assertEqual(ScheduledNotification.objects.count(), outbox_count)
        for user_id, password_hash in hashes_before.items():
            self.assertEqual(User.objects.get(id=user_id).password, password_hash)

    def test_apply_preserves_shared_parent_and_creates_exact_nine_outboxes(self):
        shared_parent_user = self.students[3656].parent.user
        shared_parent_before = (
            shared_parent_user.password,
            shared_parent_user.token_version,
            shared_parent_user.must_change_password,
        )
        historical_outboxes = list(
            ScheduledNotification.objects.order_by("id").values_list(
                "id", "status", "error_message", "origin_type", "payload"
            )
        )
        historical_logs = list(
            NotificationLog.objects.order_by("id").values_list(
                "id", "status", "success", "provider_message_id", "failure_reason"
            )
        )
        output = StringIO()

        with self.captureOnCommitCallbacks(execute=False) as callbacks:
            self._call_apply(stdout=output)

        self.assertEqual(len(callbacks), 9)
        shared_parent_user.refresh_from_db()
        self.assertEqual(
            (
                shared_parent_user.password,
                shared_parent_user.token_version,
                shared_parent_user.must_change_password,
            ),
            shared_parent_before,
        )
        for student_id, student in self.students.items():
            if student_id not in TARGET_IDS:
                continue
            student.user.refresh_from_db()
            self.assertEqual(student.user.token_version, 3)
            self.assertTrue(student.user.must_change_password)
            if student_id != 3656:
                student.parent.user.refresh_from_db()
                self.assertEqual(student.parent.user.token_version, 3)
                self.assertTrue(student.parent.user.must_change_password)

        recovery = list(
            ScheduledNotification.objects.filter(origin_type="recovery").order_by("id")
        )
        self.assertEqual(len(recovery), 9)
        self.assertTrue(
            all(row.payload.get("message_mode") == "alimtalk" for row in recovery)
        )
        self.assertTrue(
            all(row.payload.get("source_tenant_id") == self.tenant.id for row in recovery)
        )
        actual = Counter(
            (row.trigger, row.payload.get("target_id")) for row in recovery
        )
        expected = Counter(
            {
                ("registration_approved_student", "student:3656"): 1,
                **{
                    (trigger, f"{prefix}:{student_id}"): 1
                    for student_id in (4102, 4103, 4104, 4105)
                    for trigger, prefix in (
                        ("registration_approved_student", "student"),
                        ("registration_approved_parent", "parent"),
                    )
                },
            }
        )
        self.assertEqual(actual, expected)
        self.assertEqual(
            list(
                ScheduledNotification.objects.exclude(origin_type="recovery")
                .order_by("id")
                .values_list("id", "status", "error_message", "origin_type", "payload")
            ),
            historical_outboxes,
        )
        self.assertEqual(
            list(
                NotificationLog.objects.order_by("id").values_list(
                    "id", "status", "success", "provider_message_id", "failure_reason"
                )
            ),
            historical_logs,
        )
        self.assertIn("credentials_rotated=9", output.getvalue())
        self.assertIn("outboxes_created=9", output.getvalue())

    def test_pair_with_active_sibling_is_refused_fail_closed(self):
        self._create_student(5002, parent=self.students[4102].parent)

        with self.assertRaisesMessage(CommandError, "parent_shared_with_active_sibling"):
            call_command(*self._command_args())

        self.assertFalse(
            ScheduledNotification.objects.filter(origin_type="recovery").exists()
        )

    def test_any_batch_failure_rolls_back_all_five_targets(self):
        from apps.core.management.commands import repair_failed_first_enrollment_notices as command

        original_dispatch = command.dispatch_pending_account_notice

        def dispatch_or_fail(*, student_id: int):
            if student_id == 4105:
                return {"status": "pending", "enqueued": 0}
            return original_dispatch(student_id=student_id)

        user_state = {
            user.id: (user.password, user.token_version, user.must_change_password)
            for user in User.objects.filter(tenant=self.tenant)
        }
        outbox_count = ScheduledNotification.objects.count()

        with patch.object(
            command,
            "dispatch_pending_account_notice",
            side_effect=dispatch_or_fail,
        ):
            with self.assertRaisesMessage(
                CommandError,
                "replacement_outbox_pair_not_created:student_id=4105",
            ):
                self._call_apply()

        self.assertEqual(ScheduledNotification.objects.count(), outbox_count)
        self.assertFalse(
            ScheduledNotification.objects.filter(origin_type="recovery").exists()
        )
        for user_id, expected in user_state.items():
            user = User.objects.get(id=user_id)
            self.assertEqual(
                (user.password, user.token_version, user.must_change_password),
                expected,
            )

    def test_student_notice_false_rolls_back_before_parent_mutation(self):
        student = self.students[3656]
        student_before = (student.user.password, student.user.token_version)
        parent_before = (student.parent.user.password, student.parent.user.token_version)

        with patch(
            f"{COMMAND_MODULE}.send_student_account_credentials_notice",
            return_value=False,
        ):
            with self.assertRaisesMessage(
                CommandError,
                "student_replacement_outbox_not_created:student_id=3656",
            ):
                self._call_apply()

        student.user.refresh_from_db()
        student.parent.user.refresh_from_db()
        self.assertEqual((student.user.password, student.user.token_version), student_before)
        self.assertEqual((student.parent.user.password, student.parent.user.token_version), parent_before)

    def test_later_provider_sent_refuses_recovery(self):
        NotificationLog.objects.create(
            tenant=self.owner,
            source_tenant=self.tenant,
            success=True,
            status="sent",
            message_mode="alimtalk",
            notification_type="registration_approved_student",
            target_type="account",
            target_id="student:4102",
            provider_message_id="late-provider-proof",
        )

        with self.assertRaisesMessage(CommandError, "later_success_already_exists"):
            call_command(*self._command_args())

    def test_last_login_and_token_drift_are_refused(self):
        student = self.students[4102]
        student.user.last_login = timezone.now()
        student.user.save(update_fields=["last_login"])
        with self.assertRaisesMessage(CommandError, "account_already_used"):
            call_command(*self._command_args())

        student.user.last_login = None
        student.user.token_version = 3
        student.user.save(update_fields=["last_login", "token_version"])
        with self.assertRaisesMessage(CommandError, "token_version_drift"):
            call_command(*self._command_args())

    def test_template_drift_and_non_allowlisted_ids_are_refused(self):
        template = MessageTemplate.objects.get(name="registration_approved_student")
        template.solapi_status = "PENDING"
        template.save(update_fields=["solapi_status"])
        with self.assertRaisesMessage(CommandError, "owner_template_drift"):
            call_command(*self._command_args())

        with self.assertRaisesMessage(
            CommandError,
            "student_ids_must_match_reviewed_incident_allowlist",
        ):
            call_command(
                "repair_failed_first_enrollment_notices",
                "--tenant-id",
                "11",
                "--student-ids",
                "3656",
            )
