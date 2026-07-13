from unittest.mock import patch

from django.apps import apps
from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.core.models import Tenant
from apps.domains.enrollment.services.lifecycle import bulk_create_enrollments

Lecture = apps.get_model("lectures", "Lecture")
Student = apps.get_model("students", "Student")


class EnrollmentNotificationOccurrenceTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(
            code="enrollment-occurrence",
            name="Enrollment Occurrence",
            is_active=True,
        )
        user = get_user_model().objects.create_user(
            username="enrollment-occurrence-student",
            password="test1234",
            tenant=self.tenant,
        )
        self.student = Student.objects.create(
            tenant=self.tenant,
            user=user,
            ps_number="ENR001",
            omr_code="ENR00001",
            name="수강생",
            parent_phone="01012345678",
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
    @patch("apps.domains.enrollment.services.lifecycle.send_event_notification")
    def test_distinct_enrollments_get_distinct_keys_and_exact_retry_is_silent(
        self,
        send_notification,
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
        self.assertEqual(send_notification.call_count, 2)
        occurrence_keys = [
            call.kwargs["context"]["_domain_object_id"]
            for call in send_notification.call_args_list
        ]
        self.assertEqual(
            occurrence_keys,
            [f"enrollment:{first.id}", f"enrollment:{second.id}"],
        )
