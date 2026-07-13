from django.apps import apps
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.models import Tenant, TenantMembership
from apps.domains.attendance.views import AttendanceViewSet
from apps.domains.attendance.services import create_attendance_roster
from unittest.mock import patch


User = get_user_model()
Attendance = apps.get_model("attendance", "Attendance")
Enrollment = apps.get_model("enrollment", "Enrollment")
Lecture = apps.get_model("lectures", "Lecture")
Session = apps.get_model("lectures", "Session")
Student = apps.get_model("students", "Student")


class AttendanceSecessionTerminalGuardTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.tenant = Tenant.objects.create(code="att-terminal", name="Attendance Terminal", is_active=True)
        self.admin = User.objects.create_user(
            username="att-terminal-admin",
            password="test1234",
            tenant=self.tenant,
            is_staff=True,
        )
        TenantMembership.ensure_active(tenant=self.tenant, user=self.admin, role="admin")
        lecture = Lecture.objects.create(
            tenant=self.tenant,
            title="Terminal Lecture",
            name="Terminal Lecture",
            subject="MATH",
        )
        session = Session.objects.create(lecture=lecture, order=1, title="1회차")
        student_user = User.objects.create_user(
            username="att-terminal-student",
            password="test1234",
            tenant=self.tenant,
        )
        student = Student.objects.create(
            tenant=self.tenant,
            user=student_user,
            ps_number="ATTTERM001",
            omr_code="87654321",
            name="퇴원학생",
            parent_phone="01000000000",
        )
        self.enrollment = Enrollment.objects.create(
            tenant=self.tenant,
            student=student,
            lecture=lecture,
            status="INACTIVE",
        )
        self.attendance = Attendance.objects.create(
            tenant=self.tenant,
            enrollment=self.enrollment,
            session=session,
            status="SECESSION",
        )

    def _patch(self, data):
        request = self.factory.patch(
            f"/api/v1/lectures/attendance/{self.attendance.id}/",
            data,
            format="json",
        )
        request.tenant = self.tenant
        force_authenticate(request, user=self.admin)
        return AttendanceViewSet.as_view({"patch": "partial_update"})(
            request,
            pk=self.attendance.id,
        )

    def test_patch_cannot_revert_secession_to_attendance_status(self):
        response = self._patch({"status": "PRESENT"})

        self.assertEqual(response.status_code, 409, response.data)
        self.attendance.refresh_from_db()
        self.enrollment.refresh_from_db()
        self.assertEqual(self.attendance.status, "SECESSION")
        self.assertEqual(self.enrollment.status, "INACTIVE")

    def test_memo_only_patch_preserves_secession(self):
        response = self._patch({"memo": "퇴원 상담 완료"})

        self.assertEqual(response.status_code, 200, response.data)
        self.attendance.refresh_from_db()
        self.assertEqual(self.attendance.status, "SECESSION")
        self.assertEqual(self.attendance.memo, "퇴원 상담 완료")

    def test_bulk_roster_cannot_reactivate_inactive_enrollment_or_assign_fees(self):
        with patch(
            "apps.domains.attendance.services.roster.auto_assign_roster_fees"
        ) as assign_fees:
            with self.assertRaises(ValidationError):
                create_attendance_roster(
                    tenant=self.tenant,
                    session_id=self.attendance.session_id,
                    student_ids=[self.enrollment.student_id],
                )

        assign_fees.assert_not_called()
        self.enrollment.refresh_from_db()
        self.attendance.refresh_from_db()
        self.assertEqual(self.enrollment.status, "INACTIVE")
        self.assertEqual(self.attendance.status, "SECESSION")
