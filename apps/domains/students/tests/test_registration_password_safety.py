from __future__ import annotations

from unittest.mock import patch

from django.contrib.auth.hashers import make_password
from django.test import TestCase
from rest_framework.test import APIRequestFactory

from apps.core.models import Tenant
from apps.domains.students.models import StudentRegistrationRequest
from apps.domains.students.views.registration_views import (
    RegistrationRequestViewSet,
    _approve_registration_request,
)


class RegistrationPasswordSafetyTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.tenant = Tenant.objects.create(name="가입보안학원", code="regsafe", is_active=True)

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
        reg.refresh_from_db()
        self.assertEqual(reg.initial_password_plain, "")
