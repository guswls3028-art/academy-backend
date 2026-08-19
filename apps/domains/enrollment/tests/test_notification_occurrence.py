from unittest.mock import patch

from django.apps import apps
from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.core.models import Tenant
from apps.domains.enrollment.services.lifecycle import bulk_create_enrollments
from apps.domains.students.test_support import (
    create_student_account_fixture,
    create_student_fixture,
)

Lecture = apps.get_model("lectures", "Lecture")


class EnrollmentNotificationOccurrenceTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(
            code="enrollment-occurrence",
            name="Enrollment Occurrence",
            is_active=True,
        )
        self.student = create_student_account_fixture(
            tenant=self.tenant,
            password="first-password",
            student_data={
                "ps_number": "ENR001",
                "omr_code": "34567890",
                "name": "수강생",
                "phone": "01098765432",
                "parent_phone": "01012345678",
                "school_type": "HIGH",
            },
        )
        self.first_lecture = Lecture.objects.create(
            tenant=self.tenant,
            title="첫 강의",
            name="첫 강의",
            subject="MATH",
        )
        self.second_lecture = Lecture.objects.create(
            tenant=self.tenant,
            title="둘째 강의",
            name="둘째 강의",
            subject="MATH",
        )

    @patch(
        "apps.domains.enrollment.services.lifecycle.auto_assign_fees_on_enrollment"
    )
    @patch(
        "apps.support.students.account_notice_dependencies.send_welcome_messages",
        return_value={"status": "enqueued", "enqueued": 2},
    )
    def test_first_confirmed_enrollment_sends_once_and_clears_secret(
        self,
        send_welcome,
        _assign_fees,
    ):
        with self.captureOnCommitCallbacks(execute=True):
            first = bulk_create_enrollments(
                tenant=self.tenant,
                lecture_id=self.first_lecture.id,
                student_ids=[self.student.id],
            )[0]
        with self.captureOnCommitCallbacks(execute=True):
            retry = bulk_create_enrollments(
                tenant=self.tenant,
                lecture_id=self.first_lecture.id,
                student_ids=[self.student.id],
            )[0]
        with self.captureOnCommitCallbacks(execute=True):
            second = bulk_create_enrollments(
                tenant=self.tenant,
                lecture_id=self.second_lecture.id,
                student_ids=[self.student.id],
            )[0]

        self.assertEqual(first.id, retry.id)
        self.assertNotEqual(first.id, second.id)
        send_welcome.assert_called_once()
        self.assertEqual(
            send_welcome.call_args.kwargs["student_password"],
            "first-password",
        )
        self.assertEqual(
            send_welcome.call_args.kwargs["parent_password_by_phone"],
            {"01012345678": "5678"},
        )
        self.student.refresh_from_db()
        self.assertEqual(
            self.student.pending_account_notice_student_password_ciphertext,
            "",
        )
        self.assertEqual(
            self.student.pending_account_notice_parent_password_ciphertext,
            "",
        )
        self.assertIsNone(self.student.pending_account_notice_since)

    @patch(
        "apps.domains.enrollment.services.lifecycle.auto_assign_fees_on_enrollment"
    )
    @patch(
        "apps.support.students.account_notice_dependencies.send_welcome_messages",
        side_effect=[
            {"status": "enqueued", "enqueued": 1},
            {"status": "enqueued", "enqueued": 2},
        ],
    )
    def test_incomplete_outbox_keeps_secret_for_idempotent_retry(
        self,
        send_welcome,
        _assign_fees,
    ):
        with self.captureOnCommitCallbacks(execute=True):
            bulk_create_enrollments(
                tenant=self.tenant,
                lecture_id=self.first_lecture.id,
                student_ids=[self.student.id],
            )
        self.student.refresh_from_db()
        self.assertTrue(
            self.student.pending_account_notice_student_password_ciphertext
        )

        with self.captureOnCommitCallbacks(execute=True):
            bulk_create_enrollments(
                tenant=self.tenant,
                lecture_id=self.first_lecture.id,
                student_ids=[self.student.id],
            )

        self.assertEqual(send_welcome.call_count, 2)
        self.student.refresh_from_db()
        self.assertEqual(
            self.student.pending_account_notice_student_password_ciphertext,
            "",
        )

    @patch(
        "apps.domains.enrollment.services.lifecycle.auto_assign_fees_on_enrollment"
    )
    @patch(
        "apps.support.students.account_notice_dependencies.send_welcome_messages"
    )
    def test_preexisting_student_without_pending_notice_is_silent(
        self,
        send_welcome,
        _assign_fees,
    ):
        legacy_user = get_user_model().objects.create_user(
            username="legacy-enrollment-student",
            password="test1234",
            tenant=self.tenant,
        )
        legacy_student = create_student_fixture(
            tenant=self.tenant,
            user=legacy_user,
            ps_number="LEGACY001",
            omr_code="12345678",
            name="기존 학생",
            parent_phone="01011112222",
        )

        with self.captureOnCommitCallbacks(execute=True):
            bulk_create_enrollments(
                tenant=self.tenant,
                lecture_id=self.first_lecture.id,
                student_ids=[legacy_student.id],
            )

        send_welcome.assert_not_called()

    @patch(
        "apps.domains.enrollment.services.lifecycle.auto_assign_fees_on_enrollment"
    )
    @patch(
        "apps.support.students.account_notice_dependencies.send_welcome_messages",
        side_effect=RuntimeError("messaging unavailable"),
    )
    def test_messaging_exception_does_not_fail_committed_enrollment(
        self,
        send_welcome,
        _assign_fees,
    ):
        with self.captureOnCommitCallbacks(execute=True):
            enrollment = bulk_create_enrollments(
                tenant=self.tenant,
                lecture_id=self.first_lecture.id,
                student_ids=[self.student.id],
            )[0]

        self.assertIsNotNone(enrollment.id)
        send_welcome.assert_called_once()
        self.student.refresh_from_db()
        self.assertTrue(
            self.student.pending_account_notice_student_password_ciphertext
        )
