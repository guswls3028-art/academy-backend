from __future__ import annotations

from datetime import datetime, time

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.models import Tenant, TenantMembership
from apps.domains.attendance.models import Attendance
from apps.domains.attendance.services.arrival_overview import build_arrival_overview
from apps.domains.attendance.views import AttendanceViewSet
from apps.domains.clinic.models import Session as ClinicSession
from apps.domains.clinic.models import SessionParticipant
from apps.domains.enrollment.test_support import create_enrollment_fixture
from apps.domains.lectures.models import Session as LectureSession
from apps.domains.lectures.test_support import create_lecture_fixture, create_session_fixture
from apps.domains.students.test_support import create_student_fixture


User = get_user_model()


class ArrivalOverviewTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.tenant = self._tenant("arrival-a")
        self.other_tenant = self._tenant("arrival-b")
        self.admin = self._staff(self.tenant, "arrival-admin-a")
        self.other_admin = self._staff(self.other_tenant, "arrival-admin-b")
        self.lecture = create_lecture_fixture(
            tenant=self.tenant,
            name="주말 보강반",
            title="주말 보강반",
            subject="MATH",
        )
        self.supplement = create_session_fixture(
            lecture=self.lecture,
            order=1,
            title="보강",
            session_type=LectureSession.SessionType.SUPPLEMENT,
            date=timezone.localdate(),
        )
        self.regular = create_session_fixture(
            lecture=self.lecture,
            order=2,
            title="1차시",
            session_type=LectureSession.SessionType.REGULAR,
            regular_order=1,
            date=timezone.localdate(),
        )
        self.student, self.enrollment = self._student_enrollment(
            self.tenant,
            self.lecture,
            "arrival-student-a",
            "ARRIVAL001",
        )
        self.attendance = Attendance.objects.create(
            tenant=self.tenant,
            enrollment=self.enrollment,
            session=self.supplement,
            status="UNSET",
        )
        self.regular_attendance = Attendance.objects.create(
            tenant=self.tenant,
            enrollment=self.enrollment,
            session=self.regular,
            status="UNSET",
        )

    @staticmethod
    def _tenant(code):
        return Tenant.objects.create(code=code, name=code, is_active=True)

    @staticmethod
    def _staff(tenant, username):
        user = User.objects.create_user(
            username=username,
            password="test1234",
            tenant=tenant,
            is_staff=True,
        )
        TenantMembership.ensure_active(tenant=tenant, user=user, role="admin")
        return user

    @staticmethod
    def _student_enrollment(tenant, lecture, username, ps_number):
        user = User.objects.create_user(
            username=username,
            password="test1234",
            tenant=tenant,
        )
        student = create_student_fixture(
            tenant=tenant,
            user=user,
            ps_number=ps_number,
            omr_code=ps_number[-8:],
            name=username,
            parent_phone="01000000000",
        )
        enrollment = create_enrollment_fixture(
            tenant=tenant,
            student=student,
            lecture=lecture,
            status="ACTIVE",
        )
        return student, enrollment

    def _patch(self, attendance, payload):
        request = self.factory.patch(
            f"/api/v1/lectures/attendance/{attendance.id}/",
            payload,
            format="json",
        )
        request.tenant = self.tenant
        force_authenticate(request, user=self.admin)
        return AttendanceViewSet.as_view({"patch": "partial_update"})(
            request,
            pk=attendance.id,
        )

    def test_supplement_plan_persists_but_regular_session_rejects_it(self):
        today = timezone.localdate().isoformat()

        response = self._patch(
            self.attendance,
            {
                "planned_arrival_date": today,
                "planned_arrival_time": "09:30",
                "memo": "시험지 A 준비",
            },
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.attendance.refresh_from_db()
        self.assertEqual(self.attendance.planned_arrival_date.isoformat(), today)
        self.assertEqual(self.attendance.planned_arrival_time, time(9, 30))
        self.assertEqual(self.attendance.memo, "시험지 A 준비")

        rejected = self._patch(
            self.regular_attendance,
            {"planned_arrival_date": today, "planned_arrival_time": "10:00"},
        )
        self.assertEqual(rejected.status_code, 400, rejected.data)
        self.assertIn("planned_arrival_date", rejected.data)

    def test_planned_time_requires_a_date(self):
        response = self._patch(
            self.attendance,
            {"planned_arrival_time": "09:30"},
        )

        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn("planned_arrival_time", response.data)

    def test_overview_merges_two_sources_in_two_queries_and_isolates_tenant(self):
        today = timezone.localdate()
        fixed_now = timezone.make_aware(datetime.combine(today, time(9, 0)))
        self.attendance.planned_arrival_date = today
        self.attendance.planned_arrival_time = time(9, 30)
        self.attendance.memo = "시험지 A 준비"
        self.attendance.save(
            update_fields=["planned_arrival_date", "planned_arrival_time", "memo"]
        )

        second_student, second_enrollment = self._student_enrollment(
            self.tenant,
            self.lecture,
            "arrival-student-b",
            "ARRIVAL002",
        )
        Attendance.objects.create(
            tenant=self.tenant,
            enrollment=second_enrollment,
            session=self.supplement,
            planned_arrival_date=today,
            status="UNSET",
        )
        unplanned_student, unplanned_enrollment = self._student_enrollment(
            self.tenant,
            self.lecture,
            "arrival-student-unplanned",
            "ARRIVAL003",
        )
        Attendance.objects.create(
            tenant=self.tenant,
            enrollment=unplanned_enrollment,
            session=self.supplement,
            status="UNSET",
        )
        clinic_session = ClinicSession.objects.create(
            tenant=self.tenant,
            title="오답 클리닉",
            date=today,
            start_time=time(10, 30),
            duration_minutes=60,
            location="2강의실",
            max_participants=8,
        )
        SessionParticipant.objects.create(
            tenant=self.tenant,
            session=clinic_session,
            student=second_student,
            enrollment=second_enrollment,
            status="booked",
            source="manual",
            memo="오답노트 지참",
        )

        other_lecture = create_lecture_fixture(
            tenant=self.other_tenant,
            name="다른 학원",
            title="다른 학원",
            subject="MATH",
        )
        other_session = create_session_fixture(
            lecture=other_lecture,
            order=1,
            title="보강",
            session_type=LectureSession.SessionType.SUPPLEMENT,
            date=today,
        )
        _, other_enrollment = self._student_enrollment(
            self.other_tenant,
            other_lecture,
            "arrival-student-other",
            "ARRIVAL009",
        )
        Attendance.objects.create(
            tenant=self.other_tenant,
            enrollment=other_enrollment,
            session=other_session,
            planned_arrival_date=today,
            planned_arrival_time=time(9, 15),
        )

        with CaptureQueriesContext(connection) as queries:
            overview = build_arrival_overview(tenant=self.tenant, now=fixed_now)

        self.assertEqual(len(queries), 2)
        self.assertEqual(overview["summary"], {
            "soon": 1,
            "today": 3,
            "tomorrow": 0,
            "time_unset": 1,
            "overdue": 0,
        })
        self.assertEqual(
            {item["source"] for item in overview["items"]},
            {"supplement", "clinic"},
        )
        self.assertNotIn("arrival-student-other", {
            item["student_name"] for item in overview["items"]
        })
        self.assertNotIn(unplanned_student.name, {
            item["student_name"] for item in overview["items"]
        })

    def test_overview_endpoint_uses_request_tenant(self):
        self.attendance.planned_arrival_date = timezone.localdate()
        self.attendance.save(update_fields=["planned_arrival_date"])
        request = self.factory.get("/api/v1/lectures/attendance/arrival-overview/")
        request.tenant = self.tenant
        force_authenticate(request, user=self.admin)

        response = AttendanceViewSet.as_view({"get": "arrival_overview"})(request)

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["summary"]["today"], 1)
        self.assertEqual(response.data["items"][0]["student_id"], self.student.id)
