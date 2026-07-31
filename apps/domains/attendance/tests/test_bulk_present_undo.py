from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.models import Tenant, TenantMembership
from apps.domains.attendance.models import Attendance
from apps.domains.attendance.views import AttendanceViewSet
from apps.domains.enrollment.models import Enrollment
from apps.domains.lectures.models import Lecture, Session
from apps.domains.students.models import Student


User = get_user_model()


class AttendanceBulkPresentUndoTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.tenant = Tenant.objects.create(
            code="attendance-undo",
            name="Attendance Undo",
            is_active=True,
        )
        self.other_tenant = Tenant.objects.create(
            code="attendance-undo-other",
            name="Attendance Undo Other",
            is_active=True,
        )
        self.admin = User.objects.create_user(
            username="attendance-undo-admin",
            password="test1234",
            tenant=self.tenant,
            is_staff=True,
        )
        self.other_admin = User.objects.create_user(
            username="attendance-undo-other-admin",
            password="test1234",
            tenant=self.other_tenant,
            is_staff=True,
        )
        TenantMembership.ensure_active(tenant=self.tenant, user=self.admin, role="admin")
        TenantMembership.ensure_active(
            tenant=self.other_tenant,
            user=self.other_admin,
            role="admin",
        )

        self.lecture = Lecture.objects.create(
            tenant=self.tenant,
            name="되돌리기 강의",
            title="되돌리기 강의",
        )
        self.session = Session.objects.create(
            lecture=self.lecture,
            order=1,
            title="1회차",
        )
        self.unset = self._attendance("미입력 학생", "UNSET", 1)
        self.absent = self._attendance("결석 학생", "ABSENT", 2)
        self.present = self._attendance("기존 현장 학생", "PRESENT", 3)
        self.inactive = self._attendance("퇴원 학생", "SECESSION", 4, enrollment_status="INACTIVE")

    def _attendance(self, name, attendance_status, sequence, enrollment_status="ACTIVE"):
        user = User.objects.create_user(
            username=f"attendance-undo-student-{sequence}",
            password="test1234",
            tenant=self.tenant,
        )
        student = Student.objects.create(
            tenant=self.tenant,
            user=user,
            ps_number=f"UNDO{sequence:03d}",
            omr_code=f"{sequence:08d}",
            name=name,
            parent_phone=f"0100000{sequence:04d}",
        )
        enrollment = Enrollment.objects.create(
            tenant=self.tenant,
            student=student,
            lecture=self.lecture,
            status=enrollment_status,
        )
        return Attendance.objects.create(
            tenant=self.tenant,
            enrollment=enrollment,
            session=self.session,
            status=attendance_status,
        )

    def _post(self, action, data, *, tenant=None, user=None):
        request = self.factory.post(
            f"/api/v1/lectures/attendance/{action}/",
            data=data,
            format="json",
        )
        request.tenant = tenant or self.tenant
        force_authenticate(request, user=user or self.admin)
        return AttendanceViewSet.as_view({"post": action})(request)

    def _set_present(self):
        return self._post("bulk_set_present", {"session": self.session.id})

    def test_bulk_present_returns_signed_undo_and_restores_exact_previous_statuses(self):
        response = self._set_present()

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["updated"], 2)
        self.assertEqual(response.data["undo_expires_in"], 600)
        self.assertTrue(response.data["undo_token"])

        for attendance in (self.unset, self.absent, self.present, self.inactive):
            attendance.refresh_from_db()
        self.assertEqual(self.unset.status, "PRESENT")
        self.assertEqual(self.absent.status, "PRESENT")
        self.assertEqual(self.present.status, "PRESENT")
        self.assertEqual(self.inactive.status, "SECESSION")

        undo_response = self._post(
            "bulk_undo_present",
            {"undo_token": response.data["undo_token"]},
        )

        self.assertEqual(undo_response.status_code, 200, undo_response.data)
        self.assertEqual(undo_response.data, {"restored": 2, "session": self.session.id})
        for attendance in (self.unset, self.absent, self.present, self.inactive):
            attendance.refresh_from_db()
        self.assertEqual(self.unset.status, "UNSET")
        self.assertEqual(self.absent.status, "ABSENT")
        self.assertEqual(self.present.status, "PRESENT")
        self.assertEqual(self.inactive.status, "SECESSION")

    def test_undo_is_all_or_nothing_when_one_target_changed_after_bulk_action(self):
        response = self._set_present()
        self.unset.refresh_from_db()
        self.unset.status = "LATE"
        self.unset.save(update_fields=["status"])

        undo_response = self._post(
            "bulk_undo_present",
            {"undo_token": response.data["undo_token"]},
        )

        self.assertEqual(undo_response.status_code, 409, undo_response.data)
        self.unset.refresh_from_db()
        self.absent.refresh_from_db()
        self.assertEqual(self.unset.status, "LATE")
        self.assertEqual(self.absent.status, "PRESENT")

    def test_undo_token_cannot_cross_tenant_boundary(self):
        response = self._set_present()

        undo_response = self._post(
            "bulk_undo_present",
            {"undo_token": response.data["undo_token"]},
            tenant=self.other_tenant,
            user=self.other_admin,
        )

        self.assertEqual(undo_response.status_code, 404, undo_response.data)
        self.unset.refresh_from_db()
        self.absent.refresh_from_db()
        self.assertEqual(self.unset.status, "PRESENT")
        self.assertEqual(self.absent.status, "PRESENT")

    def test_tampered_undo_token_is_rejected_without_changes(self):
        response = self._set_present()
        tampered = f"{response.data['undo_token']}tampered"

        undo_response = self._post("bulk_undo_present", {"undo_token": tampered})

        self.assertEqual(undo_response.status_code, 400, undo_response.data)
        self.unset.refresh_from_db()
        self.absent.refresh_from_db()
        self.assertEqual(self.unset.status, "PRESENT")
        self.assertEqual(self.absent.status, "PRESENT")

    def test_noop_bulk_present_has_no_undo_token(self):
        self.unset.status = "PRESENT"
        self.unset.save(update_fields=["status"])
        self.absent.status = "PRESENT"
        self.absent.save(update_fields=["status"])

        response = self._set_present()

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["updated"], 0)
        self.assertIsNone(response.data["undo_token"])
        self.assertIsNone(response.data["undo_expires_in"])
