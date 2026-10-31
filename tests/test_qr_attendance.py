"""Cross-domain QR API journey: student auth, enrollment, attendance and clinic."""

import datetime
import uuid
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.models import Tenant, TenantMembership
from apps.domains.attendance.models import Attendance, AttendanceQrPoint
from apps.domains.attendance.qr_views import AttendanceQrPointViewSet, StudentQrRecordView, StudentQrResolveView
from apps.domains.clinic.test_support import create_clinic_session_fixture, create_clinic_participant_fixture

from apps.domains.enrollment.test_support import create_enrollment_fixture, create_session_enrollment_fixture
from apps.domains.lectures.test_support import create_lecture_fixture, create_session_fixture
from apps.domains.students.test_support import create_student_fixture


class AttendanceQrTests(TestCase):
    def setUp(self):
        cache.clear()
        self.clock = patch("django.utils.timezone.now", return_value=datetime.datetime(2026, 10, 31, 4, tzinfo=datetime.timezone.utc))
        self.clock.start()
        self.addCleanup(self.clock.stop)
        self.factory = APIRequestFactory()
        self.tenant = Tenant.objects.create(code="qr-tests", name="QR", is_active=True)
        self.other = Tenant.objects.create(code="qr-other", name="Other", is_active=True)
        User = get_user_model()
        self.user = User.objects.create_user(username="qr-student", tenant=self.tenant)
        self.admin = User.objects.create_user(username="qr-admin", tenant=self.tenant, is_staff=True)
        self.parent = User.objects.create_user(username="qr-parent", tenant=self.tenant)
        for user, role in [(self.user, "student"), (self.admin, "admin"), (self.parent, "parent")]:
            TenantMembership.ensure_active(tenant=self.tenant, user=user, role=role)
        self.student = create_student_fixture(
            tenant=self.tenant, user=self.user, name="QR 학생", ps_number="QR001", omr_code="00000001",
        )
        self.lecture = create_lecture_fixture(tenant=self.tenant, name="QR 강의", title="QR 강의")
        self.session = create_session_fixture(lecture=self.lecture, order=1, title="오늘 수업", date="2026-10-31")
        self.enrollment = create_enrollment_fixture(tenant=self.tenant, student=self.student, lecture=self.lecture, status="ACTIVE")
        self.membership = create_session_enrollment_fixture(tenant=self.tenant, session=self.session, enrollment=self.enrollment)
        self.point = AttendanceQrPoint.objects.create(tenant=self.tenant, kind="class", lecture=self.lecture, label="교실")
        self.clinic = create_clinic_session_fixture(tenant=self.tenant, date="2026-10-31", start_time="12:30", duration_minutes=60, location="301호", max_participants=10)
        self.participant = create_clinic_participant_fixture(tenant=self.tenant, student=self.student, session=self.clinic, status="booked", staff_memo="선생님 기록")
        self.clinic_point = AttendanceQrPoint.objects.create(tenant=self.tenant, kind="clinic", location="301호", label="클리닉")

    def post(self, view=StudentQrRecordView, *, point=None, target=None, action="check_in", user=None, tenant=None, extra=None):
        data = {"token": str((point or self.point).token), "target_id": target or self.session.pk, "action": action}
        data.update(extra or {})
        request = self.factory.post("/api/v1/student/attendance/qr/record/", data, format="json")
        request.tenant = tenant or self.tenant
        force_authenticate(request, user=user or self.user)
        return view.as_view()(request)

    def test_class_resolve_record_duplicate_preserves_id_time_and_memo(self):
        row = Attendance.objects.create(tenant=self.tenant, session=self.session, enrollment=self.enrollment, memo="보존")
        response = self.post(StudentQrResolveView)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["choices"][0]["id"], self.session.pk)
        first = self.post()
        self.assertEqual(first.status_code, 200, first.data)
        self.assertFalse(first.data["already_recorded"])
        second = self.post()
        self.assertTrue(second.data["already_recorded"])
        row.refresh_from_db()
        self.assertEqual((row.status, row.memo), ("PRESENT", "보존"))
        self.assertEqual(Attendance.objects.count(), 1)
        self.assertEqual(row.qr_checked_in_at, first.data["checked_in_at"])
        self.assertEqual(first.data["recorded_at"], second.data["recorded_at"])
        reloaded = self.post(StudentQrResolveView)
        self.assertEqual(reloaded.data["choices"][0]["status"], "PRESENT")

    def test_missing_attendance_is_created_only_for_registered_session(self):
        self.assertEqual(self.post().status_code, 200)
        self.assertEqual(Attendance.objects.get().status, "PRESENT")

    def test_manual_absence_and_withdrawal_cannot_be_overwritten(self):
        for status in ("ABSENT", "SECESSION", "ONLINE", "EARLY_LEAVE"):
            row, _ = Attendance.objects.update_or_create(tenant=self.tenant, session=self.session, enrollment=self.enrollment, defaults={"status": status})
            self.assertEqual(self.post().status_code, 409)
            row.refresh_from_db()
            self.assertEqual(row.status, status)
            self.assertIsNone(row.qr_checked_in_at)

    def test_role_tenant_and_identity_fail_closed(self):
        for user in (self.admin, self.parent):
            self.assertEqual(self.post(user=user).status_code, 403)
        self.assertEqual(self.post(tenant=self.other).status_code, 403)
        foreign = AttendanceQrPoint.objects.create(tenant=self.other, kind="clinic", label="다른 학원")
        self.assertEqual(self.post(point=foreign).status_code, 404)
        # Caller-supplied identity never selects a different student.
        self.assertEqual(self.post(extra={"student_id": 999999}).status_code, 200)
        self.assertEqual(Attendance.objects.get().enrollment.student_id, self.student.pk)

    def test_revoked_rotated_malformed_and_out_of_day_qr(self):
        self.point.active = False
        self.point.save()
        self.assertEqual(self.post().status_code, 404)
        self.point.active = True
        old = self.point.token
        self.point.token = uuid.uuid4()
        self.point.save()
        self.assertEqual(self.post(extra={"token": str(old)}).status_code, 404)
        self.assertEqual(self.post(extra={"token": "garbage"}).status_code, 400)
        self.session.date = "2026-10-30"
        self.session.save()
        self.assertEqual(self.post().status_code, 404)
        self.assertEqual(Attendance.objects.count(), 0)

    def test_removed_or_inactive_membership_and_wrong_action_do_not_write(self):
        self.assertEqual(self.post(action="check_out").status_code, 409)
        self.enrollment.status = "INACTIVE"
        self.enrollment.save()
        self.assertEqual(self.post().status_code, 404)
        self.enrollment.status = "ACTIVE"
        self.enrollment.save()
        self.membership.delete()
        self.assertEqual(self.post().status_code, 404)
        self.assertEqual(Attendance.objects.count(), 0)

    def clinic_post(self, **kwargs):
        return self.post(point=self.clinic_point, target=self.participant.pk, **kwargs)

    def test_clinic_arrival_departure_are_explicit_idempotent_and_not_completion(self):
        self.assertEqual(self.clinic_post(action="check_out").status_code, 409)
        first = self.clinic_post()
        self.assertEqual(first.status_code, 200, first.data)
        self.assertTrue(self.clinic_post().data["already_recorded"])
        self.participant.refresh_from_db()
        self.assertIsNone(self.participant.checked_out_at)
        self.assertIsNone(self.participant.completed_at)
        last = self.clinic_post(action="check_out")
        self.assertEqual(last.status_code, 200, last.data)
        self.assertTrue(self.clinic_post(action="check_out").data["already_recorded"])
        self.participant.refresh_from_db()
        self.assertEqual(self.participant.staff_memo, "선생님 기록")
        self.assertIsNone(self.participant.completed_at)
        self.assertEqual(self.participant.checkout_mode, "arrival_recorded")
        self.assertEqual(self.participant.checked_out_by_id, self.user.pk)
        self.assertEqual(first.data["checked_in_at"], last.data["checked_in_at"])
        choices = self.post(StudentQrResolveView, point=self.clinic_point).data["choices"]
        self.assertIsNotNone(choices[0]["checked_out_at"])

    def test_clinic_pending_cancelled_location_and_time_are_rejected(self):
        for status in ("pending", "cancelled", "rejected", "no_show"):
            self.participant.status = status
            self.participant.save()
            self.assertEqual(self.clinic_post().status_code, 404)
        self.participant.status = "booked"
        self.participant.save()
        self.clinic_point.location = "다른 방"
        self.clinic_point.save()
        self.assertEqual(self.clinic_post().status_code, 404)
        self.clinic_point.location = ""
        self.clinic_point.save()
        self.clinic.start_time = "16:00"
        self.clinic.save()
        self.assertEqual(self.clinic_post().status_code, 404)

    def test_clinic_overnight_booking_window(self):
        self.clinic.date = "2026-10-30"
        self.clinic.start_time = "23:00"
        self.clinic.duration_minutes = 180
        self.clinic.save()
        with patch("django.utils.timezone.now", return_value=datetime.datetime(2026, 10, 30, 15, 30, tzinfo=datetime.timezone.utc)):
            result = self.clinic_post()
        self.assertEqual(result.status_code, 200, result.data)

    def test_clinic_actual_booking_window_and_other_student_target(self):
        self.clinic.start_time = "09:00"
        self.clinic.duration_minutes = 600
        self.clinic.save()
        self.participant.booking_start_time = "16:00"
        self.participant.booking_end_time = "17:00"
        self.participant.save()
        self.assertEqual(self.clinic_post().status_code, 404)
        self.participant.booking_start_time = "12:30"
        self.participant.booking_end_time = "13:30"
        self.participant.save()
        self.assertEqual(self.clinic_post(extra={"target_id": self.participant.pk + 10000}).status_code, 404)
        self.assertEqual(self.clinic_post().status_code, 200)

    def test_inactive_student_and_corrupt_session_tenant_cannot_write(self):
        self.clinic.tenant = self.other
        self.clinic.save()
        self.assertEqual(self.clinic_post().status_code, 404)
        self.student.deleted_at = datetime.datetime(2026, 10, 31, 4, tzinfo=datetime.timezone.utc)
        self.student.save(update_fields=["deleted_at"])
        self.assertEqual(self.post().status_code, 403)
        self.assertEqual(Attendance.objects.count(), 0)

    def test_staff_create_rotate_disable_and_tenant_validation(self):
        def staff_call(method, data, action, pk=None):
            req = getattr(self.factory, method)("/api/v1/lectures/attendance-qr/", data, format="json")
            req.tenant = self.tenant
            force_authenticate(req, user=self.admin)
            return AttendanceQrPointViewSet.as_view({method: action})(req, **({"pk": pk} if pk else {}))
        response = staff_call("post", {"label": "교실 문", "kind": "class", "lecture_id": self.lecture.pk}, "create")
        self.assertEqual(response.status_code, 201, response.data)
        point_id, old_token = response.data["id"], response.data["token"]
        rotated = staff_call("post", {}, "rotate", point_id)
        self.assertEqual(rotated.status_code, 200)
        self.assertNotEqual(rotated.data["token"], old_token)
        disabled = staff_call("patch", {"active": False}, "partial_update", point_id)
        self.assertFalse(disabled.data["active"])
        self.assertEqual(staff_call("post", {"label": "오류", "kind": "class", "lecture_id": 999999}, "create").status_code, 400)
