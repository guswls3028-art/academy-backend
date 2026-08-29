from unittest.mock import patch

from django.apps import apps
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.models import Tenant, TenantMembership
from apps.domains.attendance.models import Attendance
from apps.domains.attendance.views import AttendanceViewSet
from apps.domains.enrollment.test_support import create_enrollment_fixture
from apps.domains.lectures.test_support import (
    create_lecture_fixture,
    create_session_fixture,
)
from apps.domains.students.test_support import create_student_fixture


User = get_user_model()
ScheduledNotification = apps.get_model("messaging", "ScheduledNotification")


class AttendanceManualNotificationOnlyTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.tenant = Tenant.objects.create(
            code="attendance-manual-only",
            name="Attendance Manual Only",
            is_active=True,
        )
        self.admin = User.objects.create_user(
            username="attendance-manual-only-admin",
            password="test1234",
            tenant=self.tenant,
            is_staff=True,
        )
        TenantMembership.ensure_active(
            tenant=self.tenant,
            user=self.admin,
            role="admin",
        )
        lecture = create_lecture_fixture(
            tenant=self.tenant,
            name="Manual notification lecture",
            title="Manual notification lecture",
        )
        session = create_session_fixture(
            lecture=lecture,
            order=1,
            title="1회차",
            date=timezone.localdate(),
        )
        student_user = User.objects.create_user(
            username="attendance-manual-only-student",
            password="test1234",
            tenant=self.tenant,
        )
        student = create_student_fixture(
            tenant=self.tenant,
            user=student_user,
            ps_number="ATTMANUAL001",
            omr_code="87654321",
            name="Manual Notification Student",
            parent_phone="01000000000",
        )
        enrollment = create_enrollment_fixture(
            tenant=self.tenant,
            student=student,
            lecture=lecture,
            status="ACTIVE",
        )
        self.attendance = Attendance.objects.create(
            tenant=self.tenant,
            enrollment=enrollment,
            session=session,
            status="UNSET",
        )

    def _patch_status(self, attendance_status):
        request = self.factory.patch(
            f"/api/v1/lectures/attendance/{self.attendance.id}/",
            {"status": attendance_status},
            format="json",
        )
        request.tenant = self.tenant
        force_authenticate(request, user=self.admin)

        with patch(
            "apps.domains.attendance.views.send_event_notification",
            create=True,
        ) as send_event_notification:
            with self.captureOnCommitCallbacks(execute=True) as callbacks:
                response = AttendanceViewSet.as_view({"patch": "partial_update"})(
                    request,
                    pk=self.attendance.id,
                )

        self.assertEqual(response.status_code, 200, response.data)
        self.attendance.refresh_from_db()
        self.assertEqual(self.attendance.status, attendance_status)
        self.assertEqual(callbacks, [])
        send_event_notification.assert_not_called()
        self.assertFalse(
            ScheduledNotification.objects.filter(tenant=self.tenant).exists()
        )

    def test_present_patch_does_not_enqueue_an_automatic_arrival_notification(self):
        self._patch_status("PRESENT")

    def test_absent_patch_does_not_enqueue_an_automatic_absence_notification(self):
        self._patch_status("ABSENT")
