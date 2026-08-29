from io import BytesIO
from unittest.mock import patch

import openpyxl
from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, override_settings
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.models.tenant import Tenant
from apps.core.models.tenant_membership import TenantMembership
from apps.domains.attendance.models import Attendance
from apps.domains.attendance.utils.excel import build_attendance_excel
from apps.domains.attendance.views import AttendanceViewSet
from apps.domains.enrollment.test_support import (
    create_enrollment_fixture,
    create_session_enrollment_fixture,
)
from apps.domains.lectures.test_support import (
    create_lecture_fixture,
    create_session_fixture,
)
from apps.domains.students.test_support import create_student_fixture
from apps.infrastructure.storage.r2 import generate_presigned_get_url_excel


User = get_user_model()
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


class AttendanceExcelExportDispatchTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.tenant = Tenant.objects.create(
            name="엑셀 다운로드 학원",
            code="attendance-excel-export",
            is_active=True,
        )
        self.admin = User.objects.create_user(
            username="attendance-excel-admin",
            password="test1234",
            tenant=self.tenant,
            is_staff=True,
            name="엑셀 관리자",
        )
        TenantMembership.ensure_active(
            tenant=self.tenant,
            user=self.admin,
            role="owner",
        )
        self.lecture = create_lecture_fixture(
            tenant=self.tenant,
            title="최신 명단 강의",
            name="최신 명단 강의",
            subject="수학",
        )
        self.session = create_session_fixture(
            lecture=self.lecture,
            order=1,
            title="1차시",
        )
        student_user = User.objects.create_user(
            username="attendance-excel-student",
            password="test1234",
            tenant=self.tenant,
            name="엑셀 학생",
        )
        self.student = create_student_fixture(
            tenant=self.tenant,
            user=student_user,
            ps_number="EXCEL-1",
            omr_code="87000001",
            name="엑셀 학생",
            phone="01011112222",
            parent_phone="01033334444",
        )
        self.enrollment = create_enrollment_fixture(
            tenant=self.tenant,
            student=self.student,
            lecture=self.lecture,
        )
        create_session_enrollment_fixture(
            tenant=self.tenant,
            session=self.session,
            enrollment=self.enrollment,
        )
        Attendance.objects.create(
            tenant=self.tenant,
            session=self.session,
            enrollment=self.enrollment,
            status="PRESENT",
        )

    @patch("apps.domains.attendance.views.dispatch_job")
    def test_each_download_request_dispatches_a_fresh_snapshot(self, dispatch_job):
        dispatch_job.side_effect = (
            {"ok": True, "job_id": "fresh-export-job-1"},
            {"ok": True, "job_id": "fresh-export-job-2"},
        )

        responses = []
        for _ in range(2):
            request = self.factory.post(
                "/api/v1/lectures/attendance/excel/",
                {"lecture_id": self.lecture.id},
                format="json",
            )
            force_authenticate(request, user=self.admin)
            request.tenant = self.tenant
            responses.append(
                AttendanceViewSet.as_view({"post": "excel"})(request)
            )

        self.assertEqual([response.status_code for response in responses], [202, 202])
        self.assertEqual(
            [response.data["job_id"] for response in responses],
            ["fresh-export-job-1", "fresh-export-job-2"],
        )
        self.assertEqual(dispatch_job.call_count, 2)
        for dispatched_call in dispatch_job.call_args_list:
            dispatched = dispatched_call.kwargs
            self.assertNotIn("idempotency_key", dispatched)
            self.assertEqual(dispatched["payload"]["lecture_id"], self.lecture.id)
            self.assertEqual(
                dispatched["payload"]["tenant_id"],
                str(self.tenant.id),
            )

    def test_generated_attendance_xlsx_parses_with_current_roster(self):
        workbook, filename = build_attendance_excel(self.lecture)
        buffer = BytesIO()
        workbook.save(buffer)
        buffer.seek(0)

        parsed = openpyxl.load_workbook(buffer, read_only=True, data_only=True)
        worksheet = parsed["출결"]

        self.assertEqual(filename, f"출결_최신 명단 강의_{self.lecture.id}.xlsx")
        self.assertEqual(
            list(worksheet.iter_rows(values_only=True)),
            [
                ("학생명", "학생번호", "학부모번호", "1차시"),
                ("엑셀 학생", "01011112222", "01033334444", "현장"),
            ],
        )


class ExcelDownloadUrlTests(SimpleTestCase):
    @override_settings(R2_EXCEL_BUCKET="academy-excel-test")
    @patch("apps.infrastructure.storage.r2._get_s3_client")
    def test_presigned_excel_url_forces_xlsx_attachment_headers(self, get_client):
        client = get_client.return_value
        client.generate_presigned_url.return_value = "https://download.example/file.xlsx"

        url = generate_presigned_get_url_excel(
            key="exports/tenant/job_출결_수학반.xlsx",
            expires_in=3600,
            filename="출결_수학반.xlsx",
            content_type=XLSX_MIME,
        )

        self.assertEqual(url, "https://download.example/file.xlsx")
        client.generate_presigned_url.assert_called_once_with(
            ClientMethod="get_object",
            Params={
                "Bucket": "academy-excel-test",
                "Key": "exports/tenant/job_출결_수학반.xlsx",
                "ResponseContentDisposition": (
                    "attachment; filename=\"download.xlsx\"; "
                    "filename*=UTF-8''%EC%B6%9C%EA%B2%B0_%EC%88%98%ED%95%99%EB%B0%98.xlsx"
                ),
                "ResponseContentType": XLSX_MIME,
            },
            ExpiresIn=3600,
        )
