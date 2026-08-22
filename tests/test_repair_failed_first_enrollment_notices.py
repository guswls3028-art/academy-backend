from __future__ import annotations

from collections import Counter
from datetime import timedelta
from decimal import Decimal
from hashlib import sha256
from io import StringIO
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import make_password
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import close_old_connections, connection, transaction
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone

from apps.core.models import PendingPasswordReset, Tenant
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


RECOVERY_TEST_SETTINGS = {
    "OWNER_TENANT_ID": 1,
    "TEST_TENANT_ID": 9999,
    "MESSAGING_PROVIDER_LOW_BALANCE_ALERT_THRESHOLD": 10_000,
    "MESSAGING_TENANT_BINDING_KEY": "test-credential-recovery-binding",
    "SOLAPI_API_KEY": "test-api-key",
    "SOLAPI_API_SECRET": "test-api-secret",
    "SOLAPI_SENDER": "0212345678",
    "SOLAPI_KAKAO_PF_ID": "test-common-pfid",
    "SITE_URL": "https://test.hakwonplus.com",
    "PASSWORD_HASHERS": ["django.contrib.auth.hashers.MD5PasswordHasher"],
}


class RecoveryFixtureMixin:
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

        self.sender_patcher = patch(
            f"{COMMAND_MODULE}.get_active_sender_numbers",
            return_value=[RECOVERY_TEST_SETTINGS["SOLAPI_SENDER"]],
        )
        self.sender_patcher.start()
        self.addCleanup(self.sender_patcher.stop)
        self.template_list_patcher = patch(
            f"{COMMAND_MODULE}.list_kakao_templates",
            side_effect=self._live_provider_templates,
        )
        self.template_list_patcher.start()
        self.addCleanup(self.template_list_patcher.stop)

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
        self._unrelated_password_reset_parent_history(self.students[3656])
        for student_id in (4102, 4103, 4104, 4105):
            self._failed_pair(self.students[student_id])

    def _create_templates(self):
        bodies = {
            "registration_approved_student": (
                "#{학생이름} #{학생아이디} #{학생비밀번호} "
                "#{사이트링크} #{비밀번호안내}"
            ),
            "registration_approved_parent": (
                "#{학생이름} #{학생아이디} #{학생비밀번호} "
                "#{학부모아이디} #{학부모비밀번호} #{사이트링크} #{비밀번호안내}"
            ),
        }
        for trigger, template_id in (
            ("registration_approved_student", "provider-student"),
            ("registration_approved_parent", "provider-parent"),
        ):
            template = MessageTemplate.objects.create(
                tenant=self.owner,
                category=MessageTemplate.Category.SIGNUP,
                name=trigger,
                body=bodies[trigger],
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

    def _live_provider_templates(self, *_args, **_kwargs):
        return [
            {
                "templateId": template.solapi_template_id,
                "status": "APPROVED",
                "channelId": RECOVERY_TEST_SETTINGS["SOLAPI_KAKAO_PF_ID"],
                "content": template.body,
            }
            for template in MessageTemplate.objects.filter(
                tenant=self.owner,
                name__in=(
                    "registration_approved_student",
                    "registration_approved_parent",
                ),
            ).order_by("name")
        ]

    def _create_student(self, student_id: int, *, parent: Parent | None = None) -> Student:
        student_phone = f"010{student_id:08d}"
        ps_number = f"RECOVERY-{student_id}"
        if parent is None:
            parent_phone = f"011{student_id:08d}"
            parent_user = User.objects.create(
                username=parent_phone,
                password=make_password(
                    f"old-parent-{student_id}",
                    hasher="md5",
                ),
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

        student_user = User.objects.create(
            username=ps_number,
            password=make_password(
                f"old-student-{student_id}",
                hasher="md5",
            ),
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
            ps_number=ps_number,
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
        outbox_id: int | None = None,
        dispatch_key: str | None = None,
        error_message: str = "",
        origin_type: str = "excel_import",
        origin_id: str | None = None,
        business_idempotency_key: str = "",
    ) -> ScheduledNotification:
        prefix = "student" if trigger.endswith("student") else "parent"
        target_id = f"{prefix}:{student.id}"
        resolved_origin_id = (
            f"excel-job-{student.id}" if origin_id is None else origin_id
        )
        values = {
            "tenant": self.tenant,
            "trigger": trigger,
            "send_at": timezone.now(),
            "payload": {
                "target_id": target_id,
                "event_type": trigger,
                "message_mode": "alimtalk",
                "source_tenant_id": self.tenant.id,
                "origin_type": origin_type,
                "origin_id": resolved_origin_id,
                "redacted": True,
            },
            "business_idempotency_key": business_idempotency_key,
            "origin_type": origin_type,
            "origin_id": resolved_origin_id,
            "status": status,
            "error_message": error_message,
        }
        if outbox_id is not None:
            values["id"] = outbox_id
        if dispatch_key is not None:
            values["dispatch_key"] = dispatch_key
        return ScheduledNotification.objects.create(**values)

    def _failed_pair(self, student: Student):
        self._outbox(
            student=student,
            trigger="registration_approved_student",
            status=ScheduledNotification.Status.FAILED,
            error_message="business_tenant_messaging_disabled",
            business_idempotency_key=f"failed-student-{student.id}",
        )
        self._outbox(
            student=student,
            trigger="registration_approved_parent",
            status=ScheduledNotification.Status.FAILED,
            error_message="business_tenant_messaging_disabled",
            business_idempotency_key=f"failed-parent-{student.id}",
        )

    def _student_only_history(self, student: Student):
        legacy_blank_origin_outbox = self._outbox(
            student=student,
            trigger="registration_approved_student",
            status=ScheduledNotification.Status.SENT,
            outbox_id=1174,
            dispatch_key="e3b6c52e-1890-4ee9-b549-60d789a8507b",
            origin_type="",
            origin_id="",
            business_idempotency_key=(
                "f1645e709a33ffa71c1687743eccf169"
                "774a583f02fd1995f06736c434788a69"
            ),
        )
        legacy_payload = dict(legacy_blank_origin_outbox.payload)
        legacy_payload.pop("origin_type")
        legacy_payload.pop("origin_id")
        ScheduledNotification.objects.filter(pk=legacy_blank_origin_outbox.pk).update(
            payload=legacy_payload
        )
        self._outbox(
            student=student,
            trigger="registration_approved_student",
            status=ScheduledNotification.Status.SENT,
            outbox_id=1654,
            dispatch_key="3055120a-c519-487e-b4ac-20b8057bc588",
            origin_type="system_account",
            origin_id=f"student:{student.id}",
            business_idempotency_key=(
                "6403f10f32e0633115ffd041b1e1888"
                "22abb4c7bdded5b8ab66277dcfb40bcbb"
            ),
        )
        self._outbox(
            student=student,
            trigger="registration_approved_parent",
            status=ScheduledNotification.Status.SENT,
            outbox_id=1759,
            dispatch_key="707ce6d8-756d-4a1f-86ff-1c5eb26811de",
            origin_type="credential_incident",
            origin_id="godmin-20260822",
            business_idempotency_key=(
                "ac83900afd8620f05e14a4d37fa3305"
                "4367d63446bfd9a9e4564707dd051e4b0"
            ),
        )
        NotificationLog.objects.create(
            id=4570,
            tenant=self.owner,
            source_tenant=self.tenant,
            success=False,
            status="failed",
            message_mode="alimtalk",
            notification_type="registration_approved_student",
            target_type="account",
            target_id=f"student:{student.id}",
            provider_message_id="",
            amount_deducted=Decimal("0"),
            business_idempotency_key=(
                "f1645e709a33ffa71c1687743eccf169"
                "774a583f02fd1995f06736c434788a69"
            ),
            origin_type="",
            origin_id="",
            failure_reason="provider_quota_exceeded_not_accepted",
        )
        NotificationLog.objects.create(
            id=5060,
            tenant=self.owner,
            source_tenant=self.tenant,
            success=False,
            status="ambiguous",
            message_mode="alimtalk",
            notification_type="registration_approved_student",
            target_type="account",
            target_id=f"student:{student.id}",
            provider_message_id="",
            amount_deducted=Decimal("0"),
            business_idempotency_key=(
                "6403f10f32e0633115ffd041b1e1888"
                "22abb4c7bdded5b8ab66277dcfb40bcbb"
            ),
            origin_type="system_account",
            origin_id=f"student:{student.id}",
            failure_reason=(
                "('NotEnoughBalance', '보유 잔액이 부족하여 발송에 실패하였습니다.\\n"
                "[차감금액: 13, 보유잔액: 9, 보유포인트: 0, 보유예치금: 0]')"
            ),
        )
        NotificationLog.objects.create(
            id=5145,
            tenant=self.owner,
            source_tenant=self.tenant,
            success=True,
            status="sent",
            message_mode="alimtalk",
            notification_type="registration_approved_parent",
            target_type="account",
            target_id=f"parent:{student.id}",
            provider_message_id="provider-parent-proof",
            amount_deducted=Decimal("0"),
            business_idempotency_key=(
                "ac83900afd8620f05e14a4d37fa3305"
                "4367d63446bfd9a9e4564707dd051e4b0"
            ),
            origin_type="credential_incident",
            origin_id="godmin-20260822",
            failure_reason="",
        )

    def _unrelated_password_reset_parent_history(self, student: Student):
        outbox = self._outbox(
            student=student,
            trigger="password_reset_parent",
            status=ScheduledNotification.Status.SENT,
            origin_type="legacy_password_reset",
            origin_id="redacted-legacy-origin",
            business_idempotency_key="legacy-password-reset-parent-business-key",
        )
        NotificationLog.objects.create(
            tenant=self.owner,
            source_tenant=self.tenant,
            success=True,
            status="sent",
            message_mode="alimtalk",
            notification_type="password_reset_parent",
            target_type="account",
            target_id=f"parent:{student.id}",
            provider_message_id="legacy-provider-proof",
            amount_deducted=Decimal("0"),
            business_idempotency_key=outbox.business_idempotency_key,
            origin_type=outbox.origin_type,
            origin_id=outbox.origin_id,
            failure_reason="",
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


@override_settings(**RECOVERY_TEST_SETTINGS)
class RepairFailedFirstEnrollmentNoticesTests(RecoveryFixtureMixin, TestCase):

    def test_dry_run_is_secret_free_and_does_not_mutate(self):
        output = StringIO()
        outbox_count = ScheduledNotification.objects.count()
        hashes_before = dict(self.original_password_hashes)

        self.assertEqual(
            set(
                ScheduledNotification.objects.filter(
                    trigger__in=(
                        "registration_approved_student",
                        "registration_approved_parent",
                    ),
                    payload__target_id__in=("student:3656", "parent:3656"),
                ).values_list("id", flat=True)
            ),
            {1174, 1654, 1759},
        )
        self.assertEqual(
            set(
                NotificationLog.objects.filter(
                    notification_type__in=(
                        "registration_approved_student",
                        "registration_approved_parent",
                    ),
                    target_id__in=("student:3656", "parent:3656"),
                ).values_list("id", flat=True)
            ),
            {4570, 5060, 5145},
        )
        self.assertEqual(
            {
                row.id: sha256(
                    row.business_idempotency_key.encode("utf-8")
                ).hexdigest()
                for row in ScheduledNotification.objects.filter(
                    id__in=(1174, 1654, 1759)
                )
            },
            {
                1174: "8050d4118fb4e540f3b98b3afe28110fa9a6fad1820ed8c5d5427f39eb37416d",
                1654: "9316b583e88b5f0613dc59f2efbb9ea720598499add2229e71a80838ca632e07",
                1759: "7ffab7837325994335e0cb5756528b8cc64eebd9cc0919a67da833e605134025",
            },
        )

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

    def test_unrelated_password_reset_parent_history_is_outside_incident_set(self):
        output = StringIO()

        call_command(*self._command_args(), stdout=output)

        text = output.getvalue()
        for expected in (
            "mode=dry-run",
            "candidates=5",
            "student_only=1",
            "pairs=4",
            "expected_credentials_rotated=9",
            "expected_outboxes=9",
        ):
            self.assertIn(expected, text)
        for unrelated_sentinel in (
            "legacy_password_reset",
            "redacted-legacy-origin",
            "legacy-password-reset-parent-business-key",
            "legacy-provider-proof",
        ):
            self.assertNotIn(unrelated_sentinel, text)

    def test_apply_preserves_shared_parent_and_creates_exact_nine_outboxes(self):
        from apps.core.management.commands import repair_failed_first_enrollment_notices as command

        shared_parent_user = self.students[3656].parent.user
        shared_parent_before = (
            shared_parent_user.password,
            shared_parent_user.token_version,
            shared_parent_user.must_change_password,
        )
        historical_outboxes = list(
            ScheduledNotification.objects.order_by("id").values_list(
                "id",
                "tenant_id",
                "trigger",
                "status",
                "error_message",
                "dispatch_key",
                "business_idempotency_key",
                "origin_type",
                "origin_id",
                "payload",
            )
        )
        historical_logs = list(
            NotificationLog.objects.order_by("id").values_list(
                "id",
                "tenant_id",
                "source_tenant_id",
                "status",
                "success",
                "provider_message_id",
                "failure_reason",
                "amount_deducted",
                "business_idempotency_key",
                "message_mode",
                "notification_type",
                "target_type",
                "target_id",
                "origin_type",
                "origin_id",
            )
        )
        output = StringIO()
        atomic_started = {"value": False}
        original_set_timeout = command._set_recovery_lock_timeout

        def mark_atomic_started():
            atomic_started["value"] = True
            return original_set_timeout()

        def outside_atomic_only(original):
            def wrapped(*args, **kwargs):
                self.assertFalse(atomic_started["value"])
                return original(*args, **kwargs)

            return wrapped

        with patch.object(
            command,
            "_set_recovery_lock_timeout",
            side_effect=mark_atomic_started,
        ), patch.object(
            command,
            "_assert_live_provider_contract",
            side_effect=outside_atomic_only(command._assert_live_provider_contract),
        ), patch.object(
            command,
            "get_solapi_client",
            side_effect=outside_atomic_only(command.get_solapi_client),
        ), patch.object(
            command,
            "get_queue_client",
            side_effect=outside_atomic_only(command.get_queue_client),
        ):
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
                .values_list(
                    "id",
                    "tenant_id",
                    "trigger",
                    "status",
                    "error_message",
                    "dispatch_key",
                    "business_idempotency_key",
                    "origin_type",
                    "origin_id",
                    "payload",
                )
            ),
            historical_outboxes,
        )
        self.assertEqual(
            list(
                NotificationLog.objects.order_by("id").values_list(
                    "id",
                    "tenant_id",
                    "source_tenant_id",
                    "status",
                    "success",
                    "provider_message_id",
                    "failure_reason",
                    "amount_deducted",
                    "business_idempotency_key",
                    "message_mode",
                    "notification_type",
                    "target_type",
                    "target_id",
                    "origin_type",
                    "origin_id",
                )
            ),
            historical_logs,
        )
        self.assertIn("credentials_rotated=9", output.getvalue())
        self.assertIn("outboxes_created=9", output.getvalue())

    def test_pair_with_active_sibling_is_refused_fail_closed(self):
        self._create_student(5002, parent=self.students[4102].parent)

        with self.assertRaisesMessage(CommandError, "parent_shared_with_any_student"):
            call_command(*self._command_args())

        self.assertFalse(
            ScheduledNotification.objects.filter(origin_type="recovery").exists()
        )

    def test_pair_with_inactive_sibling_is_refused_fail_closed(self):
        sibling = self._create_student(5002, parent=self.students[4102].parent)
        sibling.user.is_active = False
        sibling.user.save(update_fields=["is_active"])

        with self.assertRaisesMessage(CommandError, "parent_shared_with_any_student"):
            call_command(*self._command_args())

    def test_pair_with_soft_deleted_sibling_is_refused_fail_closed(self):
        sibling = self._create_student(5002, parent=self.students[4102].parent)
        sibling.deleted_at = timezone.now()
        sibling.save(update_fields=["deleted_at"])

        with self.assertRaisesMessage(CommandError, "parent_shared_with_any_student"):
            call_command(*self._command_args())

    def test_cross_tenant_parent_reference_is_refused_fail_closed(self):
        other_tenant = Tenant.objects.create(
            name="격리 검증 학원",
            code="cross-tenant-parent-drift",
            is_active=True,
        )
        other_user = User.objects.create_user(
            username="CROSS-TENANT-STUDENT",
            password="cross-tenant-password",
            tenant=other_tenant,
            phone="01099990001",
            is_active=False,
        )
        Student.objects.create(
            id=6001,
            tenant=other_tenant,
            user=other_user,
            parent=self.students[4102].parent,
            ps_number="CROSS-TENANT-STUDENT",
            omr_code="99990001",
            name="격리 검증 학생",
            phone="01099990001",
            parent_phone=self.students[4102].parent_phone,
        )

        with self.assertRaisesMessage(
            CommandError,
            "cross_tenant_parent_sharing_drift",
        ):
            call_command(*self._command_args())

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

        with self.assertRaisesMessage(
            CommandError,
            "provider_delivery_history_exists",
        ):
            call_command(*self._command_args())

    def test_all_noncanonical_provider_acceptance_evidence_is_refused(self):
        variants = (
            {"provider_message_id": "corrupt-provider-proof"},
            {"amount_deducted": Decimal("1")},
            {"success": True},
            {"status": "sent"},
            {"status": "sending"},
            {"status": "processing"},
            {"status": "ambiguous"},
        )
        for index, variant in enumerate(variants):
            with self.subTest(variant=variant):
                values = {
                    "tenant": self.owner,
                    "source_tenant": self.tenant,
                    "success": False,
                    "status": "failed",
                    "amount_deducted": Decimal("0"),
                    "message_mode": "alimtalk",
                    "notification_type": "registration_approved_student",
                    "target_type": "account",
                    "target_id": "student:4102",
                    "failure_reason": "corrupt_noncanonical_delivery_state",
                    "business_idempotency_key": f"corrupt-acceptance-{index}",
                }
                values.update(variant)
                log = NotificationLog.objects.create(**values)
                with self.assertRaisesMessage(
                    CommandError,
                    "provider_delivery_history_exists",
                ):
                    call_command(*self._command_args())
                log.delete()

    def test_generic_ambiguous_student_result_is_refused(self):
        log = NotificationLog.objects.get(pk=5060)
        log.failure_reason = "provider_result_unresolved"
        log.save(update_fields=["failure_reason"])

        with self.assertRaisesMessage(
            CommandError,
            "reviewed_student_log_pair_drift",
        ):
            call_command(*self._command_args())

    def test_reviewed_failure_reason_rejects_actual_line_feed(self):
        log = NotificationLog.objects.get(pk=5060)
        self.assertIn("\\n", log.failure_reason)
        self.assertNotIn("\n", log.failure_reason)
        log.failure_reason = log.failure_reason.replace("\\n", "\n")
        log.save(update_fields=["failure_reason"])

        with self.assertRaisesMessage(
            CommandError,
            "reviewed_student_log_pair_drift",
        ):
            call_command(*self._command_args())

    def test_student_provider_provenance_drift_is_refused(self):
        log = NotificationLog.objects.get(pk=5060)
        original = {
            "tenant_id": log.tenant_id,
            "source_tenant_id": log.source_tenant_id,
            "business_idempotency_key": log.business_idempotency_key,
        }
        variants = (
            {"business_idempotency_key": "wrong-historical-business-key"},
            {"tenant_id": self.tenant.id},
            {"source_tenant_id": None},
        )
        for variant in variants:
            with self.subTest(variant=variant):
                NotificationLog.objects.filter(pk=log.pk).update(**variant)
                with self.assertRaisesMessage(
                    CommandError,
                    "reviewed_student_log_pair_drift",
                ):
                    call_command(*self._command_args())
                NotificationLog.objects.filter(pk=log.pk).update(**original)

    def test_parent_success_must_match_historical_outbox_business_key(self):
        log = NotificationLog.objects.get(pk=5145)
        log.business_idempotency_key = "wrong-parent-business-key"
        log.save(update_fields=["business_idempotency_key"])

        with self.assertRaisesMessage(CommandError, "reviewed_student_log_pair_drift"):
            call_command(*self._command_args())

    def test_parent_success_requires_nonblank_provider_id(self):
        NotificationLog.objects.filter(pk=5145).update(provider_message_id=" \t ")

        with self.assertRaisesMessage(CommandError, "reviewed_student_log_pair_drift"):
            call_command(*self._command_args())

    def test_reviewed_system_account_outbox_origin_drift_is_refused(self):
        outbox = ScheduledNotification.objects.get(pk=1654)
        outbox.origin_type = "excel_import"
        outbox.save(update_fields=["origin_type"])

        with self.assertRaisesMessage(
            CommandError,
            "reviewed_student_outbox_pair_drift",
        ):
            call_command(*self._command_args())

    def test_reviewed_student_outbox_row_and_payload_drift_are_refused(self):
        for outbox_id in (1174, 1654, 1759):
            outbox = ScheduledNotification.objects.get(pk=outbox_id)
            original = {
                "tenant_id": outbox.tenant_id,
                "trigger": outbox.trigger,
                "status": outbox.status,
                "error_message": outbox.error_message,
                "dispatch_key": outbox.dispatch_key,
                "business_idempotency_key": outbox.business_idempotency_key,
                "origin_type": outbox.origin_type,
                "origin_id": outbox.origin_id,
                "payload": dict(outbox.payload),
            }
            scalar_variants = {
                "tenant_id": self.owner.id,
                "trigger": (
                    "registration_approved_parent"
                    if outbox.trigger == "registration_approved_student"
                    else "registration_approved_student"
                ),
                "status": ScheduledNotification.Status.FAILED,
                "error_message": "unexpected_terminal_error",
                "dispatch_key": f"00000000-0000-0000-0000-{outbox_id:012d}",
                "business_idempotency_key": "0" * 64,
                "origin_type": "drifted_origin",
                "origin_id": "drifted-origin-id",
            }
            for field, value in scalar_variants.items():
                with self.subTest(outbox_id=outbox_id, row_field=field):
                    ScheduledNotification.objects.filter(pk=outbox_id).update(
                        **{field: value}
                    )
                    with self.assertRaisesMessage(CommandError, "reviewed_student"):
                        call_command(*self._command_args())
                    ScheduledNotification.objects.filter(pk=outbox_id).update(
                        **original
                    )

            payload_variants = {
                "event_type": (
                    "registration_approved_parent"
                    if outbox.trigger == "registration_approved_student"
                    else "registration_approved_student"
                ),
                "target_id": "student:999999",
                "message_mode": "sms",
                "source_tenant_id": self.owner.id,
                "origin_type": "drifted_payload_origin",
                "origin_id": "drifted-payload-origin-id",
            }
            for field, value in payload_variants.items():
                with self.subTest(outbox_id=outbox_id, payload_field=field):
                    payload = dict(original["payload"])
                    payload[field] = value
                    ScheduledNotification.objects.filter(pk=outbox_id).update(
                        payload=payload
                    )
                    with self.assertRaisesMessage(CommandError, "reviewed_student"):
                        call_command(*self._command_args())
                    ScheduledNotification.objects.filter(pk=outbox_id).update(
                        **original
                    )

    def test_reviewed_payload_origin_normalization_is_blank_only(self):
        legacy_payload = ScheduledNotification.objects.get(pk=1174).payload
        self.assertNotIn("origin_type", legacy_payload)
        self.assertNotIn("origin_id", legacy_payload)

        explicit_blank_payload = {
            **legacy_payload,
            "origin_type": "",
            "origin_id": "",
        }
        ScheduledNotification.objects.filter(pk=1174).update(
            payload=explicit_blank_payload
        )
        call_command(*self._command_args(), stdout=StringIO())
        ScheduledNotification.objects.filter(pk=1174).update(payload=legacy_payload)

        for outbox_id in (1654, 1759):
            outbox = ScheduledNotification.objects.get(pk=outbox_id)
            original_payload = dict(outbox.payload)
            for field in ("origin_type", "origin_id"):
                for variant in ("missing", "blank", "wrong"):
                    with self.subTest(
                        outbox_id=outbox_id,
                        payload_field=field,
                        variant=variant,
                    ):
                        payload = dict(original_payload)
                        if variant == "missing":
                            payload.pop(field)
                        elif variant == "blank":
                            payload[field] = ""
                        else:
                            payload[field] = "drifted-payload-origin"
                        ScheduledNotification.objects.filter(pk=outbox_id).update(
                            payload=payload
                        )
                        with self.assertRaisesMessage(
                            CommandError,
                            "reviewed_student_outbox_pair_drift",
                        ):
                            call_command(*self._command_args())
                        ScheduledNotification.objects.filter(pk=outbox_id).update(
                            payload=original_payload
                        )

        for field in ("origin_type", "origin_id"):
            for value in (None, False, 0, [], {}, "drifted-payload-origin"):
                with self.subTest(
                    outbox_id=1174,
                    payload_field=field,
                    value=value,
                ):
                    payload = dict(legacy_payload)
                    payload[field] = value
                    ScheduledNotification.objects.filter(pk=1174).update(
                        payload=payload
                    )
                    with self.assertRaisesMessage(
                        CommandError,
                        "reviewed_student_outbox_pair_drift",
                    ):
                        call_command(*self._command_args())
                    ScheduledNotification.objects.filter(pk=1174).update(
                        payload=legacy_payload
                    )

    def test_reviewed_student_log_field_drift_is_refused(self):
        for log_id in (4570, 5060, 5145):
            log = NotificationLog.objects.get(pk=log_id)
            original = {
                "tenant_id": log.tenant_id,
                "source_tenant_id": log.source_tenant_id,
                "notification_type": log.notification_type,
                "target_type": log.target_type,
                "target_id": log.target_id,
                "message_mode": log.message_mode,
                "status": log.status,
                "success": log.success,
                "provider_message_id": log.provider_message_id,
                "amount_deducted": log.amount_deducted,
                "business_idempotency_key": log.business_idempotency_key,
                "origin_type": log.origin_type,
                "origin_id": log.origin_id,
                "failure_reason": log.failure_reason,
            }
            variants = {
                "tenant_id": self.tenant.id,
                "source_tenant_id": self.owner.id,
                "notification_type": (
                    "registration_approved_parent"
                    if log.notification_type == "registration_approved_student"
                    else "registration_approved_student"
                ),
                "target_type": "student",
                "target_id": "student:999999",
                "message_mode": "sms",
                "status": "processing",
                "success": not log.success,
                "provider_message_id": (
                    "" if log.provider_message_id else "unexpected-provider-proof"
                ),
                "amount_deducted": Decimal("1"),
                "business_idempotency_key": "0" * 64,
                "origin_type": "drifted_origin",
                "origin_id": "drifted-origin-id",
                "failure_reason": "drifted_failure_reason",
            }
            for field, value in variants.items():
                with self.subTest(log_id=log_id, field=field):
                    NotificationLog.objects.filter(pk=log_id).update(**{field: value})
                    with self.assertRaisesMessage(CommandError, "reviewed_student"):
                        call_command(*self._command_args())
                    NotificationLog.objects.filter(pk=log_id).update(**original)

    def test_reviewed_student_history_extra_rows_are_refused(self):
        extra_outbox = self._outbox(
            student=self.students[3656],
            trigger="registration_approved_student",
            status=ScheduledNotification.Status.SENT,
            origin_type="unexpected_history",
            origin_id="unexpected-outbox",
        )
        with self.assertRaisesMessage(CommandError, "reviewed_student_outbox_set_drift"):
            call_command(*self._command_args())
        extra_outbox.delete()

        NotificationLog.objects.create(
            tenant=self.owner,
            source_tenant=self.tenant,
            success=False,
            status="failed",
            message_mode="alimtalk",
            notification_type="registration_approved_student",
            target_type="account",
            target_id="student:3656",
            failure_reason="unexpected_extra_history",
        )
        with self.assertRaisesMessage(CommandError, "reviewed_student_log_set_drift"):
            call_command(*self._command_args())

    def test_reviewed_student_relevant_acceptance_evidence_is_refused(self):
        NotificationLog.objects.create(
            tenant=self.owner,
            source_tenant=self.tenant,
            success=True,
            status="sent",
            message_mode="alimtalk",
            notification_type="registration_approved_student",
            target_type="account",
            target_id="student:3656",
            provider_message_id="unexpected-later-provider-proof",
            amount_deducted=Decimal("1"),
            business_idempotency_key="unexpected-later-acceptance-business-key",
            origin_type="later_history",
            origin_id="student:3656",
            failure_reason="",
        )

        with self.assertRaisesMessage(CommandError, "reviewed_student_log_set_drift"):
            call_command(*self._command_args())

    def test_reviewed_student_relevant_pending_outbox_is_refused(self):
        self._outbox(
            student=self.students[3656],
            trigger="registration_approved_student",
            status=ScheduledNotification.Status.PENDING,
            origin_type="unexpected_pending_history",
            origin_id="student:3656",
        )

        with self.assertRaisesMessage(CommandError, "reviewed_student_outbox_set_drift"):
            call_command(*self._command_args())

    def test_reviewed_student_cross_tenant_relevant_history_is_refused(self):
        extra_outbox = self._outbox(
            student=self.students[3656],
            trigger="registration_approved_parent",
            status=ScheduledNotification.Status.SENT,
            origin_type="unexpected_cross_tenant_history",
            origin_id="parent:3656",
        )
        ScheduledNotification.objects.filter(pk=extra_outbox.pk).update(
            tenant_id=self.owner.id
        )
        with self.assertRaisesMessage(CommandError, "reviewed_student_outbox_set_drift"):
            call_command(*self._command_args())
        ScheduledNotification.objects.filter(pk=extra_outbox.pk).delete()

        NotificationLog.objects.create(
            tenant=self.tenant,
            source_tenant=self.owner,
            success=False,
            status="failed",
            message_mode="alimtalk",
            notification_type="registration_approved_parent",
            target_type="account",
            target_id="parent:3656",
            failure_reason="unexpected_cross_tenant_history",
        )
        with self.assertRaisesMessage(CommandError, "reviewed_student_log_set_drift"):
            call_command(*self._command_args())

    def test_reviewed_student_missing_outbox_is_refused(self):
        ScheduledNotification.objects.filter(pk=1174).delete()

        with self.assertRaisesMessage(CommandError, "reviewed_student_outbox_set_drift"):
            call_command(*self._command_args())

    def test_reviewed_student_missing_log_is_refused(self):
        NotificationLog.objects.filter(pk=4570).delete()

        with self.assertRaisesMessage(CommandError, "reviewed_student_log_set_drift"):
            call_command(*self._command_args())

    def test_failed_pair_outbox_provenance_drift_is_refused(self):
        outbox = next(
            row
            for row in ScheduledNotification.objects.filter(
                trigger="registration_approved_student"
            )
            if row.payload.get("target_id") == "student:4102"
        )
        original_tenant_id = outbox.tenant_id
        original_payload = dict(outbox.payload)
        variants = (
            ("tenant_id", self.owner.id),
            ("source_tenant_id", self.owner.id),
            ("origin_id", "wrong-original-origin"),
        )
        for field, value in variants:
            with self.subTest(field=field):
                if field == "tenant_id":
                    ScheduledNotification.objects.filter(pk=outbox.pk).update(
                        tenant_id=value
                    )
                else:
                    payload = dict(original_payload)
                    payload[field] = value
                    ScheduledNotification.objects.filter(pk=outbox.pk).update(
                        payload=payload
                    )
                with self.assertRaisesMessage(CommandError, "outbox_not_eligible"):
                    call_command(*self._command_args())
                ScheduledNotification.objects.filter(pk=outbox.pk).update(
                    tenant_id=original_tenant_id,
                    payload=original_payload,
                )

    def test_account_identifier_and_usable_password_drift_are_refused(self):
        student = self.students[4102]
        original_ps_number = student.ps_number
        Student.objects.filter(pk=student.pk).update(ps_number="DRIFTED-STUDENT-ID")
        with self.assertRaisesMessage(CommandError, "student_account_identifier_drift"):
            call_command(*self._command_args())

        Student.objects.filter(pk=student.pk).update(ps_number=original_ps_number)
        student.parent.user.set_unusable_password()
        student.parent.user.save(update_fields=["password"])
        with self.assertRaisesMessage(CommandError, "account_password_unusable"):
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

    def test_pending_password_reset_drift_is_refused(self):
        student = self.students[4102]
        PendingPasswordReset.objects.create(
            tenant=self.tenant,
            user=student.user,
            password_hash=make_password("pending-reset-secret"),
            expires_at=timezone.now() + timedelta(minutes=30),
        )

        with self.assertRaisesMessage(CommandError, "pending_password_reset_exists"):
            call_command(*self._command_args())

    def test_balance_and_queue_drift_are_refused(self):
        self.solapi_client.get_balance.return_value = SimpleNamespace(balance="9999")
        with self.assertRaisesMessage(
            CommandError,
            "provider_balance_below_recovery_threshold",
        ):
            call_command(*self._command_args())

        self.solapi_client.get_balance.return_value = SimpleNamespace(balance="42911.1")
        self.queue_client.get_queue_counts.return_value = {
            "visible": 1,
            "not_visible": 0,
            "delayed": 0,
        }
        with self.assertRaisesMessage(CommandError, "messaging_queue_not_empty"):
            call_command(*self._command_args())

    def test_committed_dispatching_claim_fails_db_quiescence(self):
        baseline_outboxes = ScheduledNotification.objects.count()
        baseline_tokens = dict(
            User.objects.filter(id__in=self.original_password_hashes).values_list(
                "id",
                "token_version",
            )
        )
        ScheduledNotification.objects.create(
            tenant=self.tenant,
            trigger="clinic_reminder",
            send_at=timezone.now(),
            payload={"target_id": "student:999999", "message_mode": "alimtalk"},
            status=ScheduledNotification.Status.DISPATCHING,
        )

        with self.assertRaisesMessage(
            CommandError,
            "recovery_quiescence_unavailable",
        ):
            self._call_apply(stdout=StringIO())

        self.assertEqual(
            ScheduledNotification.objects.count(),
            baseline_outboxes + 1,
        )
        self.assertEqual(
            dict(
                User.objects.filter(id__in=self.original_password_hashes).values_list(
                    "id",
                    "token_version",
                )
            ),
            baseline_tokens,
        )
        self.assertFalse(
            ScheduledNotification.objects.filter(origin_type="recovery").exists()
        )

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

    def test_placeholder_and_live_provider_channel_drift_are_refused(self):
        template = MessageTemplate.objects.get(name="registration_approved_student")
        original_body = template.body
        template.body = f"{original_body} #{{미승인변수}}"
        template.save(update_fields=["body"])
        with self.assertRaisesMessage(
            CommandError,
            "owner_template_placeholder_drift",
        ):
            call_command(*self._command_args())

        template.body = original_body
        template.save(update_fields=["body"])
        live_templates = self._live_provider_templates()
        live_templates[0]["channelId"] = "wrong-pfid"
        with patch(
            f"{COMMAND_MODULE}.list_kakao_templates",
            return_value=live_templates,
        ):
            with self.assertRaisesMessage(
                CommandError,
                "live_provider_template_drift",
            ):
                call_command(*self._command_args())

    def test_apply_dispatches_only_through_post_commit_outbox_callbacks(self):
        with patch(
            "apps.domains.messaging.scheduled.process_due_notifications"
        ) as process_due:
            with self.captureOnCommitCallbacks(execute=True) as callbacks:
                self._call_apply()

        self.assertEqual(len(callbacks), 9)
        self.assertEqual(process_due.call_count, 9)
        dispatched_ids = {
            call.kwargs["notification_ids"][0] for call in process_due.call_args_list
        }
        self.assertEqual(
            dispatched_ids,
            set(
                ScheduledNotification.objects.filter(origin_type="recovery").values_list(
                    "id", flat=True
                )
            ),
        )


@override_settings(**RECOVERY_TEST_SETTINGS)
class RepairFailedFirstEnrollmentNoticesPostgresConcurrencyTests(
    RecoveryFixtureMixin,
    TransactionTestCase,
):
    @classmethod
    def setUpClass(cls):
        if connection.vendor != "postgresql":
            raise unittest.SkipTest(
                "PostgreSQL is required for credential recovery row-lock verification."
            )
        from django.apps import apps as django_apps

        cls.available_apps = [
            app_config.name for app_config in django_apps.get_app_configs()
        ]
        super().setUpClass()

    def _assert_writer_lock_fails_closed(
        self,
        mutate,
        *,
        expected_scheduled_delta: int = 0,
        expected_token_updates: dict[int, int] | None = None,
    ):
        writer_ready = threading.Event()
        release_writer = threading.Event()
        writer_errors: list[Exception] = []
        scheduled_count_before = ScheduledNotification.objects.count()
        token_versions_before = dict(
            User.objects.filter(id__in=self.original_password_hashes).values_list(
                "id",
                "token_version",
            )
        )
        expected_token_versions = {
            **token_versions_before,
            **(expected_token_updates or {}),
        }

        def writer():
            close_old_connections()
            try:
                with transaction.atomic():
                    mutate()
                    writer_ready.set()
                    if not release_writer.wait(timeout=10):
                        raise TimeoutError("credential recovery concurrency writer timed out")
            except Exception as exc:
                writer_errors.append(exc)
            finally:
                close_old_connections()

        writer_thread = threading.Thread(target=writer, name="credential-drift-writer")
        with patch(
            "apps.domains.messaging.scheduled.process_due_notifications"
        ) as process_due:
            writer_thread.start()
            self.assertTrue(writer_ready.wait(timeout=10))
            started = time.monotonic()
            with self.assertRaises(CommandError) as raised:
                self._call_apply(stdout=StringIO())
            elapsed = time.monotonic() - started
            self.assertEqual(
                str(raised.exception),
                "recovery_quiescence_unavailable",
            )
            driver_error = getattr(raised.exception.__cause__, "__cause__", None)
            sqlstate = getattr(driver_error, "pgcode", None) or getattr(
                driver_error,
                "sqlstate",
                None,
            )
            self.assertEqual(sqlstate, "55P03")
            release_writer.set()
            writer_thread.join(timeout=15)

        self.assertFalse(writer_thread.is_alive())
        self.assertEqual(writer_errors, [])
        self.assertLess(elapsed, 4)
        self.assertEqual(
            ScheduledNotification.objects.count(),
            scheduled_count_before + expected_scheduled_delta,
        )
        self.assertEqual(
            dict(
                User.objects.filter(id__in=self.original_password_hashes).values_list(
                    "id",
                    "token_version",
                )
            ),
            expected_token_versions,
        )
        self.assertFalse(
            ScheduledNotification.objects.filter(origin_type="recovery").exists()
        )
        self.assertFalse(
            User.objects.filter(
                id__in=self.original_password_hashes,
                must_change_password=True,
            ).exists()
        )
        for user_id, password_hash in self.original_password_hashes.items():
            self.assertEqual(User.objects.get(pk=user_id).password, password_hash)
        process_due.assert_not_called()

    def test_concurrent_last_login_drift_aborts_without_dispatch(self):
        user_id = self.students[4102].user_id

        def mutate():
            user = User.objects.select_for_update(of=("self",)).get(pk=user_id)
            user.last_login = timezone.now()
            user.save(update_fields=["last_login"])

        self._assert_writer_lock_fails_closed(mutate)

    def test_concurrent_token_drift_aborts_without_dispatch(self):
        user_id = self.students[4102].user_id

        def mutate():
            user = User.objects.select_for_update(of=("self",)).get(pk=user_id)
            user.token_version = 3
            user.save(update_fields=["token_version"])

        self._assert_writer_lock_fails_closed(
            mutate,
            expected_token_updates={user_id: 3},
        )

    def test_concurrent_shared_parent_drift_aborts_without_dispatch(self):
        student_id = self.students[4102].id
        shared_parent_id = self.students[3656].parent_id

        def mutate():
            student = Student.objects.select_for_update(of=("self",)).get(pk=student_id)
            student.parent_id = shared_parent_id
            student.parent_phone = self.students[3656].parent_phone
            student.save(update_fields=["parent", "parent_phone"])

        self._assert_writer_lock_fails_closed(mutate)

    def test_concurrent_provider_log_drift_aborts_without_dispatch(self):
        log_id = 5060

        def mutate():
            log = NotificationLog.objects.select_for_update(of=("self",)).get(pk=log_id)
            log.failure_reason = "provider_result_unresolved"
            log.save(update_fields=["failure_reason"])

        self._assert_writer_lock_fails_closed(mutate)

    def test_concurrent_outbox_drift_aborts_without_dispatch(self):
        outbox_id = next(
            row.id
            for row in ScheduledNotification.objects.filter(
                trigger="registration_approved_student",
                status=ScheduledNotification.Status.FAILED,
            )
            if row.payload.get("target_id") == "student:4102"
        )

        def mutate():
            outbox = ScheduledNotification.objects.select_for_update(of=("self",)).get(
                pk=outbox_id
            )
            outbox.status = ScheduledNotification.Status.PENDING
            outbox.save(update_fields=["status"])

        self._assert_writer_lock_fails_closed(mutate)

    def test_concurrent_sibling_insert_blocks_then_aborts_recovery(self):
        parent_id = self.students[4102].parent_id
        parent_phone = self.students[4102].parent_phone

        def mutate():
            sibling_user = User.objects.create_user(
                username="CONCURRENT-INSERT-SIBLING",
                password="concurrent-insert-password",
                tenant=self.tenant,
                phone="01099990003",
                is_active=False,
            )
            Student.objects.create(
                id=6003,
                tenant=self.tenant,
                user=sibling_user,
                parent_id=parent_id,
                ps_number="CONCURRENT-INSERT-SIBLING",
                omr_code="99990003",
                name="동시 sibling insert",
                phone="01099990003",
                parent_phone=parent_phone,
            )

        self._assert_writer_lock_fails_closed(mutate)

    def test_concurrent_notification_log_insert_blocks_then_aborts_recovery(self):
        def mutate():
            NotificationLog.objects.create(
                tenant=self.owner,
                source_tenant=self.tenant,
                success=False,
                status="processing",
                message_mode="alimtalk",
                notification_type="registration_approved_student",
                target_type="account",
                target_id="student:4102",
                failure_reason="concurrent_insert_delivery_state",
            )

        self._assert_writer_lock_fails_closed(mutate)

    def test_concurrent_scheduled_outbox_insert_blocks_then_aborts_recovery(self):
        def mutate():
            ScheduledNotification.objects.create(
                tenant=self.tenant,
                trigger="registration_approved_student",
                send_at=timezone.now(),
                payload={
                    "target_id": "student:4102",
                    "event_type": "registration_approved_student",
                    "message_mode": "alimtalk",
                    "source_tenant_id": self.tenant.id,
                },
                origin_type="concurrent_insert",
                origin_id="student:4102",
                status=ScheduledNotification.Status.PENDING,
            )

        self._assert_writer_lock_fails_closed(mutate, expected_scheduled_delta=1)

    def test_concurrent_pending_password_reset_insert_fails_closed(self):
        def mutate():
            PendingPasswordReset.objects.create(
                tenant=self.tenant,
                user_id=self.students[4102].user_id,
                password_hash=make_password(
                    "concurrent-pending-reset",
                    hasher="md5",
                ),
                expires_at=timezone.now() + timedelta(minutes=30),
            )

        self._assert_writer_lock_fails_closed(mutate)

    def test_existing_target_row_holder_fails_closed_immediately(self):
        writer_ready = threading.Event()
        release_writer = threading.Event()
        writer_errors: list[Exception] = []
        user_id = self.students[4102].user_id
        scheduled_count_before = ScheduledNotification.objects.count()
        token_versions_before = dict(
            User.objects.filter(id__in=self.original_password_hashes).values_list(
                "id",
                "token_version",
            )
        )

        def writer():
            close_old_connections()
            try:
                with transaction.atomic():
                    User.objects.select_for_update(of=("self",)).get(pk=user_id)
                    writer_ready.set()
                    if not release_writer.wait(timeout=10):
                        raise TimeoutError("row lock writer did not release")
            except Exception as exc:
                writer_errors.append(exc)
            finally:
                close_old_connections()

        writer_thread = threading.Thread(target=writer)
        writer_thread.start()
        self.assertTrue(writer_ready.wait(timeout=10))
        with patch(
            "apps.domains.messaging.scheduled.process_due_notifications"
        ) as process_due:
            started = time.monotonic()
            with self.assertRaises(CommandError) as raised:
                self._call_apply(stdout=StringIO())
            elapsed = time.monotonic() - started
            self.assertEqual(
                str(raised.exception),
                "recovery_quiescence_unavailable",
            )
            driver_error = getattr(raised.exception.__cause__, "__cause__", None)
            sqlstate = getattr(driver_error, "pgcode", None) or getattr(
                driver_error,
                "sqlstate",
                None,
            )
            self.assertEqual(sqlstate, "55P03")

        release_writer.set()
        writer_thread.join(timeout=15)
        self.assertFalse(writer_thread.is_alive())
        self.assertEqual(writer_errors, [])
        self.assertLess(elapsed, 4)
        self.assertEqual(
            ScheduledNotification.objects.count(),
            scheduled_count_before,
        )
        self.assertEqual(
            dict(
                User.objects.filter(id__in=self.original_password_hashes).values_list(
                    "id",
                    "token_version",
                )
            ),
            token_versions_before,
        )
        self.assertFalse(
            ScheduledNotification.objects.filter(origin_type="recovery").exists()
        )
        process_due.assert_not_called()

    def test_command_first_table_lock_serializes_later_writer(self):
        from apps.core.management.commands import repair_failed_first_enrollment_notices as command

        recovery_locked = threading.Event()
        release_recovery = threading.Event()
        writer_started = threading.Event()
        recovery_errors: list[Exception] = []
        writer_errors: list[Exception] = []
        backend_pids: dict[str, int] = {}
        original_load_candidates = command._load_candidates

        def gated_load_candidates(*args, **kwargs):
            if kwargs.get("lock"):
                recovery_locked.set()
                if not release_recovery.wait(timeout=10):
                    raise TimeoutError("recovery table-lock gate timed out")
            return original_load_candidates(*args, **kwargs)

        def recover():
            close_old_connections()
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_backend_pid()")
                    backend_pids["recovery"] = int(cursor.fetchone()[0])
                self._call_apply(stdout=StringIO())
            except Exception as exc:
                recovery_errors.append(exc)
            finally:
                close_old_connections()

        def writer():
            close_old_connections()
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_backend_pid()")
                    backend_pids["writer"] = int(cursor.fetchone()[0])
                writer_started.set()
                with transaction.atomic():
                    NotificationLog.objects.create(
                        tenant=self.owner,
                        source_tenant=self.tenant,
                        success=False,
                        status="failed",
                        message_mode="alimtalk",
                        notification_type="registration_approved_student",
                        target_type="account",
                        target_id="student:999999",
                        failure_reason="later_unrelated_writer",
                    )
            except Exception as exc:
                writer_errors.append(exc)
            finally:
                close_old_connections()

        recovery_thread = threading.Thread(target=recover, name="credential-recovery")
        writer_thread = threading.Thread(target=writer, name="later-log-writer")
        with patch.object(
            command,
            "_load_candidates",
            side_effect=gated_load_candidates,
        ), patch(
            "apps.domains.messaging.scheduled.process_due_notifications"
        ) as process_due:
            recovery_thread.start()
            self.assertTrue(recovery_locked.wait(timeout=10))
            expected_table_locks = {
                PendingPasswordReset._meta.db_table,
                Student._meta.db_table,
                ScheduledNotification._meta.db_table,
                NotificationLog._meta.db_table,
            }
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT relation.relname, lock.mode, lock.granted
                    FROM pg_locks AS lock
                    JOIN pg_class AS relation ON relation.oid = lock.relation
                    WHERE lock.pid = %s AND lock.locktype = 'relation'
                    """,
                    [backend_pids["recovery"]],
                )
                relation_locks = set(cursor.fetchall())
            self.assertTrue(
                {
                    (table_name, "ShareRowExclusiveLock", True)
                    for table_name in expected_table_locks
                }.issubset(relation_locks)
            )
            writer_thread.start()
            self.assertTrue(writer_started.wait(timeout=10))

            blocked_by_recovery = False
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                with connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT pg_blocking_pids(%s)",
                        [backend_pids["writer"]],
                    )
                    blocking_pids = {int(value) for value in cursor.fetchone()[0]}
                if backend_pids["recovery"] in blocking_pids:
                    blocked_by_recovery = True
                    break
                time.sleep(0.05)

            release_recovery.set()
            recovery_thread.join(timeout=20)
            writer_thread.join(timeout=20)

        self.assertTrue(blocked_by_recovery, "later writer never waited on recovery")
        self.assertFalse(recovery_thread.is_alive())
        self.assertFalse(writer_thread.is_alive())
        self.assertEqual(recovery_errors, [])
        self.assertEqual(writer_errors, [])
        self.assertEqual(
            ScheduledNotification.objects.filter(origin_type="recovery").count(),
            9,
        )
        self.assertEqual(process_due.call_count, 9)

    def test_outer_rollback_discards_all_outboxes_and_callbacks(self):
        from apps.core.management.commands import repair_failed_first_enrollment_notices as command

        original_dispatch = command.dispatch_pending_account_notice
        scheduled_count_before = ScheduledNotification.objects.count()
        token_versions_before = dict(
            User.objects.filter(id__in=self.original_password_hashes).values_list(
                "id",
                "token_version",
            )
        )

        def dispatch_or_fail(*, student_id: int):
            if student_id == 4105:
                return {"status": "pending", "enqueued": 0}
            return original_dispatch(student_id=student_id)

        with patch.object(
            command,
            "dispatch_pending_account_notice",
            side_effect=dispatch_or_fail,
        ), patch(
            "apps.domains.messaging.scheduled.process_due_notifications"
        ) as process_due:
            with self.assertRaisesMessage(
                CommandError,
                "replacement_outbox_pair_not_created:student_id=4105",
            ):
                self._call_apply(stdout=StringIO())

        self.assertFalse(
            ScheduledNotification.objects.filter(origin_type="recovery").exists()
        )
        self.assertEqual(
            ScheduledNotification.objects.count(),
            scheduled_count_before,
        )
        self.assertEqual(
            dict(
                User.objects.filter(id__in=self.original_password_hashes).values_list(
                    "id",
                    "token_version",
                )
            ),
            token_versions_before,
        )
        self.assertEqual(connection.run_on_commit, [])
        process_due.assert_not_called()
        for user_id, password_hash in self.original_password_hashes.items():
            user = User.objects.get(pk=user_id)
            self.assertEqual(user.password, password_hash)
            self.assertFalse(user.must_change_password)
