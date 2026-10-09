from django.test import TestCase

from apps.core.models import TenantMembership
from apps.domains.enrollment.test_support import (
    create_enrollment_fixture,
    create_session_enrollment_fixture,
)
from apps.domains.student_app.sessions.views import (
    StudentSessionHideView,
    StudentSessionListView,
    StudentSessionUnhideView,
)
from apps.domains.student_app.tests.test_session_tenant_isolation import (
    _create_lecture,
    _create_session,
    _create_student,
    _create_tenant,
    _create_user,
    _request,
)


class StudentScheduleInputBoundaryTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.tenant = _create_tenant("schedule-input")
        cls.user = _create_user(cls.tenant, "schedule-student")
        cls.student = _create_student(cls.tenant, cls.user, "ScheduleStudent")
        TenantMembership.ensure_active(tenant=cls.tenant, user=cls.user, role="student")
        cls.lecture = _create_lecture(cls.tenant, "Schedule lecture")
        cls.session = _create_session(cls.lecture)
        cls.enrollment = create_enrollment_fixture(
            tenant=cls.tenant, student=cls.student, lecture=cls.lecture, status="ACTIVE",
        )
        cls.membership = create_session_enrollment_fixture(
            tenant=cls.tenant, enrollment=cls.enrollment, session=cls.session,
        )

    def test_invalid_free_text_time_does_not_break_student_schedule(self):
        for lecture_time, expected_start in (
            ("토 25:00 ~ 26:00", None),
            ("일 12:99", None),
            ("토 24:00", None),
            ("토 112:00", None),
            ("일 12:000", None),
            ("시간 협의", None),
            ("토 09:05 ~ 10:00", "09:05:00"),
            ("일 00:00 ~ 01:00", "00:00:00"),
        ):
            with self.subTest(lecture_time=lecture_time):
                self.lecture.lecture_time = lecture_time
                self.lecture.save(update_fields=["lecture_time", "updated_at"])
                response = StudentSessionListView().get(_request(self.user, self.tenant))
                self.assertEqual(response.status_code, 200)
                self.assertEqual([item["id"] for item in response.data], [self.session.id])
                self.assertEqual(response.data[0]["start_time"], expected_start)
                self.lecture.refresh_from_db()
                self.assertEqual(self.lecture.lecture_time, lecture_time)

    def test_invalid_schedule_ids_do_not_hide_or_unhide_other_items(self):
        invalid_ids = (float(self.session.id) + 0.5, True, False, 0, 2**63,
                       -(2**63), str(2**63), "1.5", "1e0", "9" * 100, None, {}, [])
        for view_class in (StudentSessionHideView, StudentSessionUnhideView):
            for value in invalid_ids:
                with self.subTest(view=view_class.__name__, value=value):
                    self.student.schedule_hidden_ids = [self.session.id, -77]
                    self.student.save(update_fields=["schedule_hidden_ids", "updated_at"])
                    response = view_class().post(_request(self.user, self.tenant, {"id": value}))
                    self.assertEqual(response.status_code, 400)
                    self.student.refresh_from_db()
                    self.assertEqual(self.student.schedule_hidden_ids, [self.session.id, -77])

    def test_integer_and_string_ids_keep_hide_undo_and_repeat_behavior(self):
        response = StudentSessionHideView().post(
            _request(self.user, self.tenant, {"id": str(self.session.id)}),
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["hidden_ids"], [self.session.id])
        response = StudentSessionHideView().post(
            _request(self.user, self.tenant, {"id": self.session.id}),
        )
        self.assertEqual(response.data["hidden_ids"], [self.session.id])
        self.student.schedule_hidden_ids = [self.session.id, -77]
        self.student.save(update_fields=["schedule_hidden_ids", "updated_at"])
        for target in (str(self.session.id), "-77"):
            response = StudentSessionUnhideView().post(
                _request(self.user, self.tenant, {"id": target}),
            )
            self.assertEqual(response.status_code, 200)
        self.student.refresh_from_db()
        self.assertEqual(self.student.schedule_hidden_ids, [])
        self.membership.refresh_from_db()
        self.assertEqual(self.membership.session_id, self.session.id)
        self.assertEqual(self.membership.enrollment_id, self.enrollment.id)
