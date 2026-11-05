"""Cross-domain journeys for one shared lesson with multiple teaching dates."""
from datetime import date, datetime
from zoneinfo import ZoneInfo
import threading
import unittest
from unittest.mock import patch

from django.db import close_old_connections, connection, transaction
from django.test import TransactionTestCase
from rest_framework.test import force_authenticate

from apps.core.models import Tenant, TenantMembership
from apps.domains.attendance.models import Attendance, AttendanceQrPoint
from apps.domains.attendance.services.qr import class_roster
from apps.domains.attendance.views import AttendanceViewSet
from apps.domains.enrollment.models import Enrollment, SessionEnrollment
from apps.domains.lectures.models import Lecture, Session, SessionScheduleChange
from apps.domains.lectures.services.schedules import ScheduleConflict, save_schedules, schedule_state
from apps.domains.lectures.tests.test_lecture_stabilization import LectureTestBase, User
from apps.domains.lectures.views import SessionViewSet
from apps.domains.results.views.session_scores_view import SessionScoresView
from apps.domains.students.models import Student
from apps.domains.student_app.sessions.views import StudentSessionDetailView, StudentSessionListView
from apps.domains.video.models import Video, VideoProgress
from apps.support.lectures.schedule_queries import student_schedule_annotations
from apps.support.lectures.schedule_dependencies import lock_attendance_parent_rows
from apps.support.student_app.dashboard_dependencies import today_lecture_sessions_for_dashboard
from apps.support.student_app.session_dependencies import get_student_lecture_sessions


class TestSessionSchedules(LectureTestBase):
    def setUp(self):
        super().setUp()
        self.session = Session.objects.create(lecture=self.lecture, order=1, title="공통 1차시", date=date(2026, 4, 8))
        self.students, self.enrollments = [], []
        for index in range(3):
            user = User.objects.create_user(username=f"schedule-{index}", tenant=self.tenant)
            student = Student.objects.create(
                tenant=self.tenant, user=user, name=f"학생 {index}", ps_number=f"S{index}",
                phone=f"0103333{index:04d}", parent_phone=f"0104444{index:04d}", omr_code=f"3333{index:04d}",
            )
            enrollment = Enrollment.objects.create(tenant=self.tenant, lecture=self.lecture, student=student)
            SessionEnrollment.objects.create(tenant=self.tenant, session=self.session, enrollment=enrollment)
            Attendance.objects.create(tenant=self.tenant, session=self.session, enrollment=enrollment, status="UNSET")
            self.students.append(student)
            self.enrollments.append(enrollment)
            TenantMembership.ensure_active(tenant=self.tenant, user=user, role="student")

    def request(self, method, view, data=None, **kwargs):
        request = getattr(self.factory, method)("/", data or {}, format="json")
        request.tenant = self.tenant
        force_authenticate(request, user=self.admin)
        return view(request, **kwargs)

    def schedules(self, method="get", data=None, session=None):
        return self.request(method, SessionViewSet.as_view({method: "schedules"}), data, pk=(session or self.session).pk)

    def configure(self):
        state = self.schedules().data
        response = self.schedules("put", {"version": state["version"], "schedules": [
            {"label": "수요일", "date": "2026-04-08", "start_time": "17:00", "end_time": "18:00", "enrollment_ids": [self.enrollments[0].pk]},
            {"label": "목요일", "date": "2026-04-09", "enrollment_ids": [self.enrollments[1].pk]},
        ]})
        self.assertEqual(response.status_code, 200, response.data)
        return response.data

    def test_shared_video_progress_and_attendance_survive_assignment_move_and_removal(self):
        video = Video.objects.create(tenant=self.tenant, session=self.session, title="공유 영상", status="READY")
        progress = VideoProgress.objects.create(video=video, enrollment=self.enrollments[0], progress=.65)
        Attendance.objects.filter(enrollment=self.enrollments[0]).update(status="LATE")
        state = self.configure()
        self.assertEqual(state, self.schedules().data)
        state["schedules"][0]["enrollment_ids"] = []
        state["schedules"][1]["enrollment_ids"].append(self.enrollments[0].pk)
        response = self.schedules("put", state)
        self.assertEqual(response.status_code, 200, response.data)
        response = self.schedules("put", {"version": response.data["version"], "schedules": []})
        self.assertEqual(response.status_code, 200)
        progress.refresh_from_db()
        self.assertEqual(progress.progress, .65)
        self.assertEqual(video.session_id, self.session.pk)
        self.assertEqual(Attendance.objects.get(enrollment=self.enrollments[0]).status, "LATE")
        self.assertEqual(SessionEnrollment.objects.filter(session=self.session).count(), 3)
        self.assertEqual(SessionScheduleChange.objects.filter(session=self.session).count(), 3)

    def test_duplicate_foreign_invalid_and_stale_configuration_are_atomic(self):
        state = self.configure()
        other = Session.objects.create(lecture=self.lecture, order=2, title="다른 차시")
        foreign = other.schedules.create(label="외부", date=date(2026, 4, 9))
        invalid_rows = [
            [{**state["schedules"][0], "enrollment_ids": [self.enrollments[0].pk] * 2}],
            [{**state["schedules"][0], "id": foreign.pk}],
            [{**state["schedules"][0], "enrollment_ids": [True]}],
            [{**state["schedules"][0], "enrollment_ids": [9223372036854775808]}],
            [{**state["schedules"][0], "date": "2026-02-30"}],
            [{**state["schedules"][0], "end_time": "16:00"}],
        ]
        for rows in invalid_rows:
            with self.subTest(rows=rows):
                response = self.schedules("put", {"version": state["version"], "schedules": rows})
                self.assertEqual(response.status_code, 400, response.data)
                self.assertEqual(self.schedules().data, state)
        self.assertEqual(self.schedules("put", {"version": "stale", "schedules": []}).status_code, 409)
        self.assertEqual(self.schedules("post", [1]).status_code, 400)
        self.assertEqual(SessionScheduleChange.objects.count(), 1)

    def test_scope_filters_attendance_scores_and_bulk_present_with_undo(self):
        state = self.configure()
        selected = state["schedules"][1]["id"]
        response = self.request("get", AttendanceViewSet.as_view({"get": "list"}), {"session": self.session.pk, "schedule": selected})
        self.assertEqual(response.status_code, 200, response.data)
        rows = response.data.get("results", response.data) if isinstance(response.data, dict) else response.data
        self.assertEqual([r["enrollment_id"] for r in rows], [self.enrollments[1].pk])
        scores = self.request("get", SessionScoresView.as_view(), {"schedule": selected}, session_id=self.session.pk)
        self.assertEqual(scores.status_code, 200, scores.data)
        self.assertEqual([r["enrollment_id"] for r in scores.data["rows"]], [self.enrollments[1].pk])
        response = self.request("post", AttendanceViewSet.as_view({"post": "bulk_set_present"}), {"session": self.session.pk, "schedule": selected})
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["updated"], 1)
        self.assertEqual(list(Attendance.objects.order_by("enrollment_id").values_list("status", flat=True)), ["UNSET", "PRESENT", "UNSET"])
        undo = self.request("post", AttendanceViewSet.as_view({"post": "bulk_undo_present"}), {"undo_token": response.data["undo_token"]})
        self.assertEqual(undo.status_code, 200, undo.data)
        self.assertFalse(Attendance.objects.exclude(status="UNSET").exists())
        for scope in ("bad", "999999999999999999999999", "-1"):
            invalid = self.request("post", AttendanceViewSet.as_view({"post": "bulk_set_present"}), {"session": self.session.pk, "schedule": scope})
            self.assertEqual(invalid.status_code, 400)
        unassigned = self.request("get", SessionScoresView.as_view(), {"schedule": "0"}, session_id=self.session.pk)
        self.assertEqual([r["enrollment_id"] for r in unassigned.data["rows"]], [self.enrollments[2].pk])

    def test_student_calendar_today_and_qr_follow_assigned_date(self):
        self.configure()
        for day in ("2026-04-08", "2026-04-09"):
            staff = self.request("get", SessionViewSet.as_view({"get": "list"}), {"date": day, "include_progress": "1"})
            self.assertEqual(staff.status_code, 200, staff.data)
            self.assertEqual([row["id"] for row in staff.data], [self.session.pk])
            self.assertEqual(len(staff.data[0]["schedule_dates"]), 2)
            self.assertEqual(staff.data[0]["attendance_total"], 1)
            self.assertEqual(staff.data[0]["attendance_filled"], 1)
        expected = [date(2026, 4, 8), date(2026, 4, 9), None]
        point = AttendanceQrPoint.objects.create(tenant=self.tenant, kind="class", lecture=self.lecture, label="수업")
        for index, student in enumerate(self.students):
            with self.subTest(student=index):
                session = Session.objects.annotate(**student_schedule_annotations(tenant=self.tenant, student=student)).get(pk=self.session.pk)
                self.assertEqual(session.effective_date, expected[index])
                self.assertTrue(session.schedule_configured)
                today = today_lecture_sessions_for_dashboard(tenant=self.tenant, student=student, today=date(2026, 4, 9))
                self.assertEqual(today.exists(), index == 1)
                qr = class_roster(tenant=self.tenant, student=student, point=point, now=datetime(2026, 4, 9, 17, tzinfo=ZoneInfo("Asia/Seoul")))
                self.assertEqual(qr.exists(), index == 1)
                rows = get_student_lecture_sessions(session_ids=[self.session.pk], tenant=self.tenant, student=student, hidden_before=None, hidden_session_ids=set())
                self.assertEqual(rows[0].effective_date, expected[index])
                request = self.factory.get("/")
                request.tenant = self.tenant
                force_authenticate(request, user=student.user)
                detail = StudentSessionDetailView.as_view()(request, pk=self.session.pk)
                self.assertEqual(detail.status_code, 200, detail.data)
                self.assertEqual(detail.data["date"], expected[index].isoformat() if expected[index] else None)
                self.assertTrue(detail.data["schedule_configured"])
                listing = StudentSessionListView.as_view()(request)
                self.assertEqual(listing.status_code, 200, listing.data)
                self.assertEqual(listing.data[0]["date"], detail.data["date"])
                denied = SessionViewSet.as_view({"get": "schedules"})(request, pk=self.session.pk)
                self.assertEqual(denied.status_code, 403)

    def test_copy_shifts_dates_adds_active_roster_without_restoring_removed_students(self):
        self.configure()
        target = Session.objects.create(lecture=self.lecture, order=2, title="다음 차시", date=date(2026, 4, 15))
        Attendance.objects.create(tenant=self.tenant, session=target, enrollment=self.enrollments[1], status="SECESSION")
        self.enrollments[2].status = "INACTIVE"
        self.enrollments[2].save(update_fields=["status"])
        response = self.schedules("post", {"version": schedule_state(target)["version"], "source_session": self.session.pk}, target)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual([row["date"] for row in response.data["schedules"]], ["2026-04-15", "2026-04-16"])
        self.assertEqual([row["enrollment_id"] for row in response.data["roster"]], [self.enrollments[0].pk])
        self.assertEqual(Attendance.objects.get(session=target, enrollment=self.enrollments[0]).status, "UNSET")
        self.assertEqual(Attendance.objects.get(session=target, enrollment=self.enrollments[1]).status, "SECESSION")

    def test_cross_tenant_denied_and_legacy_date_unchanged(self):
        foreign_tenant = Tenant.objects.create(code="other-schedule", name="다른 학원")
        foreign_lecture = Lecture.objects.create(tenant=foreign_tenant, title="외부", name="외부")
        foreign = Session.objects.create(lecture=foreign_lecture, order=1, title="외부")
        self.assertEqual(self.schedules(session=foreign).status_code, 404)
        self.assertEqual(self.schedules("post", {"version": schedule_state(self.session)["version"], "source_session": foreign.pk}).status_code, 400)
        session = Session.objects.annotate(**student_schedule_annotations(tenant=self.tenant, student=self.students[0])).get(pk=self.session.pk)
        self.assertEqual(session.effective_date, date(2026, 4, 8))
        self.assertFalse(session.schedule_configured)

    def test_version_is_bound_to_the_lesson_and_its_reference_date(self):
        first = schedule_state(self.session)["version"]
        target = Session.objects.create(lecture=self.lecture, order=2, title="다음 차시", date=self.session.date)
        for enrollment in self.enrollments:
            SessionEnrollment.objects.create(tenant=self.tenant, session=target, enrollment=enrollment)
        response = self.schedules("put", {"version": first, "schedules": []}, target)
        self.assertEqual(response.status_code, 409)
        Session.objects.filter(pk=self.session.pk).update(date=date(2026, 4, 10))
        self.assertEqual(self.schedules("put", {"version": first, "schedules": []}).status_code, 409)

    def test_explicit_teaching_date_remains_visible_after_nominal_lecture_end(self):
        state = self.configure()
        state["schedules"][1]["date"] = "2026-05-01"
        self.assertEqual(self.schedules("put", state).status_code, 200)
        student = self.students[1]
        self.assertTrue(today_lecture_sessions_for_dashboard(tenant=self.tenant, student=student, today=date(2026, 5, 1)).exists())
        with patch("apps.support.student_app.session_dependencies.timezone.localdate", return_value=date(2026, 4, 1)):
            rows = get_student_lecture_sessions(session_ids=[self.session.pk], tenant=self.tenant, student=student, hidden_before=None, hidden_session_ids=set())
        self.assertEqual(rows[0].effective_date, date(2026, 5, 1))
        self.lecture.is_active = False
        self.lecture.save(update_fields=["is_active"])
        self.assertFalse(today_lecture_sessions_for_dashboard(tenant=self.tenant, student=student, today=date(2026, 5, 1)).exists())


class TestSessionScheduleConcurrencyPostgres(TransactionTestCase):
    @classmethod
    def setUpClass(cls):
        if connection.vendor != "postgresql":
            raise unittest.SkipTest("Actual schedule write locks require PostgreSQL")
        super().setUpClass()

    def test_only_one_editor_can_save_the_same_version(self):
        tenant = Tenant.objects.create(code="schedule-concurrency", name="동시 수정")
        lecture = Lecture.objects.create(tenant=tenant, title="강의", name="강의")
        session = Session.objects.create(lecture=lecture, order=1, title="차시")
        version = schedule_state(session)["version"]
        barrier = threading.Barrier(2, timeout=10)
        saved, errors = [], []

        def edit(label):
            close_old_connections()
            try:
                barrier.wait()
                saved.append(save_schedules(session=session, actor_id=None, data={
                    "version": version,
                    "schedules": [{"label": label, "date": "2026-04-08", "enrollment_ids": []}],
                }))
            except BaseException as error:
                errors.append(error)
            finally:
                close_old_connections()

        threads = [threading.Thread(target=edit, args=(label,)) for label in ("수요일", "목요일")]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=20)
        self.assertFalse(any(thread.is_alive() for thread in threads), "schedule save deadlock")
        self.assertEqual(len(saved), 1)
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], ScheduleConflict)
        self.assertEqual(schedule_state(session), saved[0])
        self.assertEqual(SessionScheduleChange.objects.filter(session=session).count(), 1)

    def test_copy_waits_for_lifecycle_parents_before_locking_target_session(self):
        tenant = Tenant.objects.create(code="schedule-copy-lock", name="복사 잠금")
        lecture = Lecture.objects.create(tenant=tenant, title="강의", name="강의")
        source = Session.objects.create(lecture=lecture, order=1, title="이전 차시", date=date(2026, 4, 8))
        target = Session.objects.create(lecture=lecture, order=2, title="다음 차시", date=date(2026, 4, 15))
        user = User.objects.create_user(tenant=tenant, username="copy-lock-student")
        student = Student.objects.create(tenant=tenant, user=user, name="학생", ps_number="COPY", omr_code="12345678", parent_phone="01000000000")
        enrollment = Enrollment.objects.create(tenant=tenant, lecture=lecture, student=student)
        SessionEnrollment.objects.create(tenant=tenant, session=source, enrollment=enrollment)
        save_schedules(session=source, actor_id=None, data={"version": schedule_state(source)["version"], "schedules": [
            {"label": "수요일", "date": "2026-04-08", "enrollment_ids": [enrollment.pk]},
        ]})
        version = schedule_state(target)["version"]
        attempting = threading.Event()
        results, errors = [], []

        def lock_parents(**kwargs):
            attempting.set()
            return lock_attendance_parent_rows(**kwargs)

        def copy():
            close_old_connections()
            try:
                results.append(save_schedules(session=target, actor_id=None, source_session=source.pk, data={"version": version}))
            except BaseException as error:
                errors.append(error)
            finally:
                close_old_connections()

        thread = threading.Thread(target=copy)
        with patch("apps.domains.lectures.services.schedules.lock_attendance_parent_rows", side_effect=lock_parents):
            try:
                with transaction.atomic():
                    Student.objects.select_for_update().get(pk=student.pk)
                    thread.start()
                    self.assertTrue(attempting.wait(timeout=10))
                    Session.objects.select_for_update(nowait=True).get(pk=target.pk)
            finally:
                thread.join(timeout=20)
        self.assertFalse(thread.is_alive(), "copy lock order deadlock")
        self.assertEqual(errors, [])
        self.assertEqual(results[0]["schedules"][0]["enrollment_ids"], [enrollment.pk])
