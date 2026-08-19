"""Cross-domain student/enrollment Excel password-mode integration tests."""

from __future__ import annotations

from unittest.mock import patch

from django.test import TestCase

from apps.core.models import Tenant
from apps.domains.enrollment.services import lecture_enroll_from_excel_rows
from apps.domains.lectures.models import Lecture
from apps.domains.students.models import Student
from apps.domains.students.services import (
    import_students_from_rows,
)


class StudentExcelImportPasswordModeTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(
            name="엑셀 비밀번호 학원",
            code="excel_password_modes",
            is_active=True,
        )

    @patch("apps.domains.messaging.services.send_welcome_messages")
    def test_phone_last4_sets_each_created_student_password_and_notice(self, send_mock):
        result = import_students_from_rows(
            tenant_id=self.tenant.id,
            students_data=[
                {
                    "name": "학생하나",
                    "parent_phone": "01070001111",
                    "phone": "01090001234",
                    "school_type": "HIGH",
                    "grade": 1,
                    "uses_identifier": False,
                },
                {
                    "name": "학생둘",
                    "parent_phone": "01070002222",
                    "phone": "01090005678",
                    "school_type": "HIGH",
                    "grade": 2,
                    "uses_identifier": False,
                },
            ],
            initial_password="",
            password_mode="phone_last4",
        )

        self.assertEqual(result["created"], 2)
        first = Student.objects.get(tenant=self.tenant, name="학생하나")
        second = Student.objects.get(tenant=self.tenant, name="학생둘")
        self.assertTrue(first.user.check_password("1234"))
        self.assertTrue(second.user.check_password("5678"))
        self.assertTrue(first.user.must_change_password)
        self.assertTrue(second.user.must_change_password)
        self.assertEqual(
            send_mock.call_args.kwargs["student_password_by_id"],
            {first.id: "1234", second.id: "5678"},
        )
        self.assertNotIn("credentials", result)

    def test_phone_last4_skips_invalid_row_and_creates_valid_student(self):
        result = import_students_from_rows(
            tenant_id=self.tenant.id,
            students_data=[
                {
                    "name": "번호없는학생",
                    "parent_phone": "01070003333",
                    "phone": None,
                    "school_type": "HIGH",
                    "grade": 1,
                    "uses_identifier": True,
                },
                {
                    "name": "번호있는학생",
                    "parent_phone": "01070004444",
                    "phone": "01090004321",
                    "school_type": "HIGH",
                    "grade": 1,
                    "uses_identifier": False,
                },
            ],
            initial_password="",
            password_mode="phone_last4",
        )

        self.assertEqual(result["created"], 1)
        self.assertEqual(result["failed"][0]["row"], 1)
        self.assertIn("학생 전화번호가 없어", result["failed"][0]["error"])
        self.assertFalse(Student.objects.filter(tenant=self.tenant, name="번호없는학생").exists())
        student = Student.objects.get(tenant=self.tenant, name="번호있는학생")
        self.assertTrue(student.user.check_password("4321"))

    @patch("apps.domains.messaging.services.send_welcome_messages")
    @patch("apps.domains.students.services.import_passwords.secrets.randbelow", return_value=42)
    def test_random_mode_returns_download_credentials_and_sets_password(
        self,
        _random_mock,
        send_mock,
    ):
        result = import_students_from_rows(
            tenant_id=self.tenant.id,
            students_data=[
                {
                    "name": "랜덤학생",
                    "parent_phone": "01070004444",
                    "phone": "01090009999",
                    "school_type": "HIGH",
                    "grade": 1,
                    "uses_identifier": False,
                }
            ],
            initial_password="",
            password_mode="random",
        )

        student = Student.objects.get(tenant=self.tenant, name="랜덤학생")
        self.assertTrue(student.user.check_password("0042"))
        self.assertTrue(student.user.must_change_password)
        self.assertEqual(
            result["credentials"],
            [{
                "name": "랜덤학생",
                "login_id": student.ps_number,
                "password": "0042",
            }],
        )
        self.assertEqual(
            send_mock.call_args.kwargs["student_password_by_id"],
            {student.id: "0042"},
        )

    def test_lecture_excel_import_uses_phone_last4_policy(self):
        lecture = Lecture.objects.create(
            tenant=self.tenant,
            title="비밀번호 방식 강의",
            name="비밀번호 방식 강의",
            subject="MATH",
        )

        result = lecture_enroll_from_excel_rows(
            tenant_id=self.tenant.id,
            lecture_id=lecture.id,
            students_data=[
                {
                    "name": "강의등록학생",
                    "parent_phone": "01070005555",
                    "phone": "01090008765",
                    "school_type": "HIGH",
                    "grade": 1,
                    "uses_identifier": False,
                }
            ],
            initial_password="",
            password_mode="phone_last4",
        )

        student = Student.objects.get(tenant=self.tenant, name="강의등록학생")
        self.assertTrue(student.user.check_password("8765"))
        self.assertEqual(result["created_students_count"], 1)
        self.assertEqual(result["enrolled_count"], 1)
