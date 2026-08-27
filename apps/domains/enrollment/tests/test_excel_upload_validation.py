from __future__ import annotations

from io import BytesIO
from unittest.mock import patch

import openpyxl
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.models.tenant import Tenant
from apps.core.models.tenant_membership import TenantMembership
from apps.domains.enrollment.views import EnrollmentViewSet
from apps.domains.lectures.models import Lecture

User = get_user_model()


def _valid_xlsx_upload() -> SimpleUploadedFile:
    workbook = openpyxl.Workbook()
    worksheet = workbook.active
    worksheet.append(["이름", "학부모전화번호", "학생전화번호"])
    worksheet.append(["업로드학생", "01070001111", "01090001234"])
    stream = BytesIO()
    workbook.save(stream)
    return SimpleUploadedFile(
        "students.xlsx",
        stream.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


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

    @patch("apps.infrastructure.storage.r2._get_s3_client")
    @patch("apps.domains.enrollment.views.dispatch_job")
    @patch("apps.domains.enrollment.views.upload_fileobj_to_r2_excel")
    def test_dispatch_rejection_removes_uploaded_excel(
        self,
        mock_upload,
        mock_dispatch,
        mock_get_s3_client,
    ):
        mock_dispatch.return_value = {"ok": False, "error": "dispatch unavailable"}
        request = self.factory.post(
            "/api/v1/enrollments/lecture_enroll_from_excel/",
            data={
                "file": _valid_xlsx_upload(),
                "lecture_id": self.lecture.id,
            },
            format="multipart",
        )
        force_authenticate(request, user=self.admin)
        request.tenant = self.tenant

        response = EnrollmentViewSet.as_view(
            {"post": "lecture_enroll_from_excel"}
        )(request)

        self.assertEqual(response.status_code, 400, response.data)
        uploaded_key = mock_upload.call_args.kwargs["key"]
        mock_get_s3_client.return_value.delete_object.assert_called_once_with(
            Bucket=getattr(settings, "R2_EXCEL_BUCKET", "academy-excel"),
            Key=uploaded_key,
        )

    @patch("apps.infrastructure.storage.r2._get_s3_client")
    @patch("apps.domains.enrollment.views.dispatch_job")
    @patch("apps.domains.enrollment.views.upload_fileobj_to_r2_excel")
    def test_dispatch_exception_removes_uploaded_excel_before_reraising(
        self,
        mock_upload,
        mock_dispatch,
        mock_get_s3_client,
    ):
        mock_dispatch.side_effect = RuntimeError("dispatch exploded")
        request = self.factory.post(
            "/api/v1/enrollments/lecture_enroll_from_excel/",
            data={
                "file": _valid_xlsx_upload(),
                "lecture_id": self.lecture.id,
            },
            format="multipart",
        )
        force_authenticate(request, user=self.admin)
        request.tenant = self.tenant

        with self.assertRaisesRegex(RuntimeError, "dispatch exploded"):
            EnrollmentViewSet.as_view(
                {"post": "lecture_enroll_from_excel"}
            )(request)

        uploaded_key = mock_upload.call_args.kwargs["key"]
        mock_get_s3_client.return_value.delete_object.assert_called_once_with(
            Bucket=getattr(settings, "R2_EXCEL_BUCKET", "academy-excel"),
            Key=uploaded_key,
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

    @patch("apps.domains.enrollment.views.dispatch_job")
    @patch("apps.domains.enrollment.views.upload_fileobj_to_r2_excel")
    def test_csv_is_rejected_before_r2_upload(self, mock_upload, mock_dispatch):
        upload = SimpleUploadedFile(
            "students.csv",
            "이름,학부모전화번호\n합성학생,01070001111\n".encode(),
            content_type="text/csv",
        )
        request = self.factory.post(
            "/api/v1/enrollments/lecture_enroll_from_excel/",
            data={
                "file": upload,
                "lecture_id": self.lecture.id,
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
    def test_existing_student_enrollment_does_not_require_password_configuration(
        self,
        mock_upload,
        mock_dispatch,
    ):
        mock_dispatch.return_value = {"ok": True, "job_id": "excel-job-1"}
        request = self.factory.post(
            "/api/v1/enrollments/lecture_enroll_from_excel/",
            data={
                "file": _valid_xlsx_upload(),
                "lecture_id": self.lecture.id,
            },
            format="multipart",
        )
        force_authenticate(request, user=self.admin)
        request.tenant = self.tenant

        response = EnrollmentViewSet.as_view({"post": "lecture_enroll_from_excel"})(request)

        self.assertEqual(response.status_code, 202, response.data)
        payload = mock_dispatch.call_args.kwargs["payload"]
        self.assertEqual(payload["student_match_mode"], "existing_only")
        self.assertNotIn("password_mode", payload)
        self.assertNotIn("initial_password", payload)
        self.assertNotIn("initial_password_secret", payload)
        mock_upload.assert_called_once()

    @patch("apps.domains.enrollment.views.dispatch_job")
    @patch("apps.domains.enrollment.views.upload_fileobj_to_r2_excel")
    def test_legacy_password_fields_are_not_dispatched_for_enrollment(
        self,
        mock_upload,
        mock_dispatch,
    ):
        mock_dispatch.return_value = {"ok": True, "job_id": "excel-job-fixed"}
        request = self.factory.post(
            "/api/v1/enrollments/lecture_enroll_from_excel/",
            data={
                "file": _valid_xlsx_upload(),
                "lecture_id": self.lecture.id,
                "password_mode": "fixed",
                "initial_password": "fixed-secret-1234",
            },
            format="multipart",
        )
        force_authenticate(request, user=self.admin)
        request.tenant = self.tenant

        response = EnrollmentViewSet.as_view(
            {"post": "lecture_enroll_from_excel"}
        )(request)

        self.assertEqual(response.status_code, 202, response.data)
        payload = mock_dispatch.call_args.kwargs["payload"]
        self.assertEqual(payload["student_match_mode"], "existing_only")
        self.assertNotIn("password_mode", payload)
        self.assertNotIn("initial_password", payload)
        self.assertNotIn("initial_password_secret", payload)
        self.assertNotIn("fixed-secret-1234", str(payload))
