from __future__ import annotations

from unittest.mock import patch

from django.contrib.auth.hashers import make_password
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.models import Tenant, TenantMembership
from apps.domains.parents.services import ensure_parent_account_for_student
from apps.domains.students.models import StudentRegistrationRequest
from apps.domains.students.views.registration_views import (
    RegistrationRequestViewSet,
    _approve_registration_request,
)
from apps.domains.students.views import StudentViewSet

User = get_user_model()


class RegistrationPasswordSafetyTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.tenant = Tenant.objects.create(name="가입보안학원", code="regsafe", is_active=True)
        self.admin = User.objects.create_user(
            username="regsafe-admin",
            password="test1234",
            tenant=self.tenant,
            is_staff=True,
            name="가입 관리자",
        )
        TenantMembership.ensure_active(tenant=self.tenant, user=self.admin, role="owner")

    def _registration_payload(self) -> dict:
        return {
            "name": "가입학생",
            "username": "REGSAFE01",
            "initial_password": "rawpw1234",
            "parent_phone": "01055556666",
            "phone": "01077778888",
            "school_type": "HIGH",
            "high_school": "테스트고",
            "origin_middle_school": "테스트중",
            "grade": 1,
            "gender": "M",
            "address": "서울",
        }

    def test_registration_create_does_not_persist_plain_password(self):
        request = self.factory.post(
            "/api/v1/students/registration-requests/",
            self._registration_payload(),
            format="json",
        )
        request.tenant = self.tenant

        response = RegistrationRequestViewSet.as_view({"post": "create"})(request)

        self.assertEqual(response.status_code, 201)
        reg = StudentRegistrationRequest.objects.get()
        self.assertEqual(reg.initial_password_plain, "")
        self.assertNotEqual(reg.initial_password, "rawpw1234")

    @patch("apps.domains.students.views.registration_views.send_registration_approved_messages")
    def test_approval_message_uses_non_secret_password_phrase(self, send_mock):
        reg = StudentRegistrationRequest.objects.create(
            tenant=self.tenant,
            status=StudentRegistrationRequest.PENDING,
            initial_password=make_password("rawpw1234"),
            initial_password_plain="legacy-rawpw",
            name="가입학생",
            username="REGSAFE02",
            parent_phone="01055556666",
            phone="01077778888",
            school_type="HIGH",
            high_school="테스트고",
            origin_middle_school="테스트중",
            grade=1,
            gender="M",
            address="서울",
        )
        request = self.factory.post("/api/v1/students/registration-requests/approve/")
        request.tenant = self.tenant

        error = _approve_registration_request(request, reg)

        self.assertIsNone(error)
        send_mock.assert_called_once()
        self.assertEqual(
            send_mock.call_args.kwargs["student_password"],
            "가입 신청 시 입력한 비밀번호",
        )
        self.assertEqual(send_mock.call_args.kwargs["parent_password"], "6666")
        reg.refresh_from_db()
        self.assertEqual(reg.initial_password_plain, "")

    @patch("apps.domains.students.views.registration_views.send_registration_approved_messages")
    def test_approval_message_says_parent_password_unchanged_for_existing_parent(self, send_mock):
        ensure_parent_account_for_student(
            tenant=self.tenant,
            parent_phone="01055556666",
            student_name="기존학생",
        )
        reg = StudentRegistrationRequest.objects.create(
            tenant=self.tenant,
            status=StudentRegistrationRequest.PENDING,
            initial_password=make_password("rawpw1234"),
            initial_password_plain="",
            name="가입학생",
            username="REGSAFE03",
            parent_phone="01055556666",
            phone="01077778889",
            school_type="HIGH",
            high_school="테스트고",
            origin_middle_school="테스트중",
            grade=1,
            gender="M",
            address="서울",
        )
        request = self.factory.post("/api/v1/students/registration-requests/approve/")
        request.tenant = self.tenant

        error = _approve_registration_request(request, reg)

        self.assertIsNone(error)
        self.assertEqual(send_mock.call_args.kwargs["parent_password"], "변경되지 않음")

    @patch("apps.domains.students.views.student_views.send_welcome_messages")
    def test_student_create_welcome_uses_parent_initial_password_ssot(self, send_mock):
        request = self.factory.post(
            "/api/v1/students/",
            {
                "name": "직접등록학생",
                "ps_number": "REGSAFE-CREATE-1",
                "initial_password": "stud1234",
                "parent_phone": "01055556666",
                "phone": "01077778891",
                "school_type": "HIGH",
                "grade": 1,
                "send_welcome_message": True,
            },
            format="json",
        )
        force_authenticate(request, user=self.admin)
        request.tenant = self.tenant

        response = StudentViewSet.as_view({"post": "create"})(request)

        self.assertEqual(response.status_code, 201)
        self.assertEqual(
            send_mock.call_args.kwargs["parent_password_by_phone"],
            {"01055556666": "6666"},
        )

    @patch("apps.domains.students.views.student_views.send_welcome_messages")
    def test_student_create_welcome_says_parent_password_unchanged_when_account_exists(self, send_mock):
        ensure_parent_account_for_student(
            tenant=self.tenant,
            parent_phone="01055556666",
            student_name="기존학생",
        )
        request = self.factory.post(
            "/api/v1/students/",
            {
                "name": "추가자녀",
                "ps_number": "REGSAFE-CREATE-2",
                "initial_password": "stud1234",
                "parent_phone": "01055556666",
                "phone": "01077778892",
                "school_type": "HIGH",
                "grade": 1,
                "send_welcome_message": True,
            },
            format="json",
        )
        force_authenticate(request, user=self.admin)
        request.tenant = self.tenant

        response = StudentViewSet.as_view({"post": "create"})(request)

        self.assertEqual(response.status_code, 201)
        self.assertEqual(
            send_mock.call_args.kwargs["parent_password_by_phone"],
            {"01055556666": "변경되지 않음"},
        )

    @patch("apps.domains.messaging.services.get_tenant_site_url", return_value="https://hakwonplus.com")
    @patch("apps.domains.messaging.services.send_welcome_messages")
    def test_excel_worker_welcome_uses_parent_initial_password_ssot(self, send_mock, _site_mock):
        from apps.domains.students.services.bulk_from_excel import (
            bulk_create_students_from_excel_rows,
        )

        result = bulk_create_students_from_excel_rows(
            tenant_id=self.tenant.id,
            students_data=[
                {
                    "name": "엑셀등록학생",
                    "parent_phone": "01055556666",
                    "phone": "01077778893",
                    "school_type": "HIGH",
                    "grade": 1,
                }
            ],
            initial_password="stud1234",
        )

        self.assertEqual(result["created"], 1)
        self.assertEqual(
            send_mock.call_args.kwargs["parent_password_by_phone"],
            {"01055556666": "6666"},
        )

    @patch("apps.domains.messaging.services.send_welcome_messages")
    def test_excel_worker_respects_send_welcome_message_false(self, send_mock):
        from apps.domains.students.services.bulk_from_excel import (
            bulk_create_students_from_excel_rows,
        )

        result = bulk_create_students_from_excel_rows(
            tenant_id=self.tenant.id,
            students_data=[
                {
                    "name": "엑셀무알림학생",
                    "parent_phone": "01055556666",
                    "phone": "01077778894",
                    "school_type": "HIGH",
                    "grade": 1,
                }
            ],
            initial_password="stud1234",
            send_welcome_message=False,
        )

        self.assertEqual(result["created"], 1)
        send_mock.assert_not_called()

    def test_excel_worker_payload_bool_parses_string_false(self):
        from academy.application.services.excel_parsing_service import _payload_bool

        self.assertFalse(_payload_bool("false", default=True))
        self.assertFalse(_payload_bool("0", default=True))
        self.assertTrue(_payload_bool(None, default=True))
