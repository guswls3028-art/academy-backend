from __future__ import annotations

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.models.tenant import Tenant
from apps.core.models.tenant_membership import TenantMembership
from apps.domains.enrollment.views import EnrollmentViewSet
from apps.domains.lectures.models import Lecture

User = get_user_model()


class EnrollmentExcelUploadValidationTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.tenant = Tenant.objects.create(
            name="Enrollment Excel Guard",
            code="enroll_excel_guard",
            is_active=True,
        )
        self.admin = User.objects.create_user(
            username="enroll_excel_guard_admin",
            password="test1234",
            tenant=self.tenant,
            is_staff=True,
        )
        TenantMembership.ensure_active(tenant=self.tenant, user=self.admin, role="owner")
        self.lecture = Lecture.objects.create(
            tenant=self.tenant,
            title="Excel Guard Lecture",
            name="Excel Guard Lecture",
            subject="MATH",
        )

    @patch("apps.domains.enrollment.views.dispatch_job")
    @patch("apps.domains.enrollment.views.upload_fileobj_to_r2_excel")
    def test_fake_xlsx_is_rejected_before_r2_upload(self, mock_upload, mock_dispatch):
        upload = SimpleUploadedFile(
            "bad.xlsx",
            b"not a real spreadsheet",
            content_type="application/octet-stream",
        )
        request = self.factory.post(
            "/api/v1/enrollments/lecture_enroll_from_excel/",
            data={
                "file": upload,
                "lecture_id": self.lecture.id,
                "initial_password": "0000",
            },
            format="multipart",
        )
        force_authenticate(request, user=self.admin)
        request.tenant = self.tenant

        response = EnrollmentViewSet.as_view({"post": "lecture_enroll_from_excel"})(request)

        self.assertEqual(response.status_code, 400, response.data)
        mock_upload.assert_not_called()
        mock_dispatch.assert_not_called()

    @patch("apps.domains.enrollment.views.dispatch_job")
    @patch("apps.domains.enrollment.views.upload_fileobj_to_r2_excel")
    def test_legacy_xls_is_rejected_before_r2_upload(self, mock_upload, mock_dispatch):
        upload = SimpleUploadedFile(
            "legacy.xls",
            b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1legacy",
            content_type="application/vnd.ms-excel",
        )
        request = self.factory.post(
            "/api/v1/enrollments/lecture_enroll_from_excel/",
            data={
                "file": upload,
                "lecture_id": self.lecture.id,
                "initial_password": "0000",
            },
            format="multipart",
        )
        force_authenticate(request, user=self.admin)
        request.tenant = self.tenant

        response = EnrollmentViewSet.as_view({"post": "lecture_enroll_from_excel"})(request)

        self.assertEqual(response.status_code, 400, response.data)
        mock_upload.assert_not_called()
        mock_dispatch.assert_not_called()
