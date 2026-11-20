from datetime import date, time
from uuid import uuid4
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.urls import resolve
from django.test import TestCase, override_settings
from rest_framework.test import APIRequestFactory, force_authenticate
from rest_framework.throttling import AnonRateThrottle

from apps.core.models import Tenant, TenantMembership
from apps.domains.attendance.models import Attendance
from apps.domains.enrollment.models import Enrollment, SessionEnrollment
from apps.domains.lectures.models import Lecture, Section, Session, SessionScheduleMembership
from apps.domains.students.models import Student, ClassGroup, ClassGroupLecture
from apps.domains.students.views.demo_session import DawonDemoLearningView


@override_settings(DAWON_REVIEW_CLASS_GROUPS=True)
class DawonDemoLearningTests(TestCase):
    def setUp(self):
        # The isolated demo supplies this rate. Generic SQLite test settings omit it.
        self.enterContext(patch.dict(AnonRateThrottle.THROTTLE_RATES, {"anon": "60/minute"}))
        self.tenant = Tenant.objects.create(id=719, code="dawon", name="다원")
        user = get_user_model().objects.create_user(username="sample", tenant=self.tenant)
        self.sample = Student.objects.create(
            id=2, tenant=self.tenant, user=user, name="다원 수업 검수 학생 2",
            is_managed=True, ps_number="qa-sample", omr_code="00000002",
        )

    def read(self, tenant):
        request = APIRequestFactory().get("/api/v1/students/demo-learning/")
        request.tenant = tenant
        return DawonDemoLearningView.as_view()(request)

    def test_projection_shows_owned_session_and_attendance_only(self):
        lecture = Lecture.objects.create(tenant=self.tenant, title="수업", subject="수학")
        membership = Enrollment.objects.create(tenant=self.tenant, student=self.sample, lecture=lecture)
        lesson = Session.objects.create(lecture=lecture, title="차시", order=1, date=date(2026, 1, 22))
        Session.objects.create(lecture=lecture, title="별도 차시", order=2)
        SessionEnrollment.objects.create(tenant=self.tenant, enrollment=membership, session=lesson)
        Attendance.objects.create(tenant=self.tenant, enrollment=membership, session=lesson, status="PRESENT")
        response = self.read(self.tenant)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Cache-Control"], "no-store")
        self.assertEqual(response.data["sessions"], [{
            "id": lesson.id, "lecture": "수업", "title": "차시", "date": lesson.date,
            "time": None, "attendance": "출석", "videos": [], "exams": [], "homework": [],
        }])
        membership.status = "INACTIVE"
        membership.save(update_fields=["status"])
        self.assertEqual(self.read(self.tenant).data["sessions"], [])

    def test_homework_guidance_is_limited_to_assigned_homework_and_student_fields(self):
        from apps.domains.homework.models import HomeworkAssignment
        from apps.domains.homework_results.models import Homework

        lecture = Lecture.objects.create(tenant=self.tenant, title="수학", subject="수학")
        enrollment = Enrollment.objects.create(tenant=self.tenant, student=self.sample, lecture=lecture)
        lesson = Session.objects.create(lecture=lecture, order=1, title="함수", date=date(2026, 11, 15))
        SessionEnrollment.objects.create(tenant=self.tenant, enrollment=enrollment, session=lesson)
        meta = {"student_instructions": "오답 풀이 정리", "due_date": "2026-11-17",
                "submission_guide": "오답 노트 제출", "staff_memo": "private"}
        task = Homework.objects.create(tenant=self.tenant, session=lesson, title="복습", meta=meta)
        Homework.objects.create(tenant=self.tenant, session=lesson, title="다른 학생의 과제", meta=meta)
        HomeworkAssignment.objects.create(tenant=self.tenant, session=lesson, enrollment=enrollment, homework=task)
        rows = self.read(self.tenant).data["sessions"][0]["homework"]
        self.assertEqual(rows, [{"id": task.pk, "title": "복습", "state": "needs_submission",
                                 "student_instructions": "오답 풀이 정리", "due_date": "2026-11-17",
                                 "submission_guide": "오답 노트 제출"}])
        task.meta = None
        task.save(update_fields=["meta"])
        self.assertIsNone(self.read(self.tenant).data["sessions"][0]["homework"][0]["due_date"])

    def test_scope_is_fixed(self):
        other = Tenant.objects.create(code="qa-other", name="other")
        self.assertEqual(self.read(other).status_code, 404)
        self.sample.is_managed = False
        self.sample.save(update_fields=["is_managed"])
        self.assertEqual(self.read(self.tenant).status_code, 404)
        with override_settings(DAWON_REVIEW_CLASS_GROUPS=False):
            self.assertEqual(self.read(self.tenant).status_code, 404)

    def test_new_native_regular_lesson_creates_an_attendance_roster_and_assigns_one_date(self):
        lecture = Lecture.objects.create(tenant=self.tenant, title="반 강의", subject="수학")
        group = ClassGroup.objects.create(tenant=self.tenant, name="예시 학교반", kind="school", school="예시고")
        ClassGroupLecture.objects.create(group=group, lecture=lecture, period="midterm")
        enrollment = Enrollment.objects.create(tenant=self.tenant, student=self.sample, lecture=lecture)
        owner = get_user_model().objects.create_user(username="review-owner", tenant=self.tenant)
        TenantMembership.ensure_active(tenant=self.tenant, user=owner, role="owner")

        def post(payload, schedules=False):
            route = "/api/v1/lectures/sessions/" + ("with-schedules/" if schedules else "")
            request = APIRequestFactory().post(route, payload, format="json")
            request.tenant = self.tenant
            force_authenticate(request, user=owner)
            return resolve(route).func(request)

        row = {"label":"목요일","date":"2026-11-19","start_time":"18:00","end_time":"19:00"}
        payload = {"lecture":lecture.id,"title":"수학 차시","date":row["date"],
                   "create_request_id":str(uuid4()),"initial_schedules":[row]}
        created = post(payload, True)
        self.assertEqual(created.status_code, 201, created.data)
        session_id = created.data["id"]
        replay = post(payload, True)
        self.assertEqual(replay.status_code, 201, replay.data)
        self.assertEqual(replay.data["id"], session_id)
        self.assertEqual(Attendance.objects.get(session_id=session_id,enrollment=enrollment).status, "UNSET")
        self.assertEqual(SessionScheduleMembership.objects.filter(schedule__session_id=session_id).count(), 1)
        self.assertEqual(self.read(self.tenant).data["sessions"][0]["date"], date(2026,11,19))
        second = {**row, "label":"금요일", "date":"2026-11-20"}
        multi = post({**payload, "create_request_id":str(uuid4()), "initial_schedules":[row,second]}, True)
        self.assertEqual(multi.status_code, 201, multi.data)
        self.assertTrue(SessionEnrollment.objects.filter(session_id=multi.data["id"], enrollment=enrollment).exists())
        self.assertFalse(SessionScheduleMembership.objects.filter(schedule__session_id=multi.data["id"]).exists())
        supplement = post({"lecture":lecture.pk, "title":"보강", "session_type":"SUPPLEMENT"})
        self.assertEqual(supplement.status_code, 201, supplement.data)
        self.assertFalse(SessionEnrollment.objects.filter(session_id=supplement.data["id"]).exists())
        section = Section.objects.create(tenant=self.tenant, lecture=lecture, label="A", day_of_week=3, start_time=time(18))
        specific = post({"lecture":lecture.pk, "title":"별도 반", "section":section.pk})
        self.assertEqual(specific.status_code, 201, specific.data)
        self.assertFalse(SessionEnrollment.objects.filter(session_id=specific.data["id"]).exists())
        with patch("apps.support.enrollment.lifecycle_dependencies.ensure_session_roster_membership", side_effect=RuntimeError("roster unavailable")):
            with self.assertRaisesMessage(RuntimeError, "roster unavailable"):
                post({"lecture":lecture.pk, "title":"롤백 확인"})
        self.assertFalse(Session.objects.filter(lecture=lecture, title="롤백 확인").exists())
        standalone = Lecture.objects.create(tenant=self.tenant, title="독립 강의", subject="수학")
        Enrollment.objects.create(tenant=self.tenant, student=self.sample, lecture=standalone)
        independent = post({"lecture":standalone.pk, "title":"독립 차시"})
        self.assertEqual(independent.status_code, 201, independent.data)
        self.assertFalse(SessionEnrollment.objects.filter(session_id=independent.data["id"]).exists())
        enrollment.status = "INACTIVE"
        enrollment.save(update_fields=["status"])
        regular = post({"lecture":lecture.pk, "title":"다음 회차"})
        self.assertEqual(regular.status_code, 201, regular.data)
        self.assertFalse(SessionEnrollment.objects.filter(session_id=regular.data["id"]).exists())
