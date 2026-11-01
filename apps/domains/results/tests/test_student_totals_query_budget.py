"""Student totals must not issue another set of queries for every session."""
from django.apps import apps
from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from apps.support.results.score_total_dependencies import (
    student_exam_session_pairs,
    student_session_totals,
)


class StudentTotalsQueryBudgetTests(TestCase):
    def setUp(self):
        self.tenant = apps.get_model("core", "Tenant").objects.create(code="totals-budget", name="Totals")
        self.lecture = apps.get_model("lectures", "Lecture").objects.create(
            tenant=self.tenant, title="Math", name="Math", subject="MATH",
        )
        self.enrollment = self._enrollment("one")
        self.peer = self._enrollment("two")

    def _enrollment(self, name):
        user = get_user_model().objects.create_user(username=f"totals-{name}", tenant=self.tenant)
        student = apps.get_model("students", "Student").objects.create(
            tenant=self.tenant, user=user, name=name, ps_number=name,
        )
        return apps.get_model("enrollment", "Enrollment").objects.create(
            tenant=self.tenant, lecture=self.lecture, student=student, status="ACTIVE",
        )

    def _session(self, order):
        session = apps.get_model("lectures", "Session").objects.create(
            lecture=self.lecture, order=order, title=f"Session {order}",
        )
        apps.get_model("enrollment", "SessionEnrollment").objects.create(
            tenant=self.tenant, session=session, enrollment=self.enrollment,
        )
        exam = apps.get_model("exams", "Exam").objects.create(
            tenant=self.tenant, title=f"Exam {order}", exam_type="regular", max_score=100,
        )
        exam.sessions.add(session)
        return session, exam

    def _read(self):
        with CaptureQueriesContext(connection) as queries:
            pairs = student_exam_session_pairs(tenant=self.tenant, enrollment_ids=[self.enrollment.id])
            totals = student_session_totals(
                tenant=self.tenant, exam_rows=[], homework_rows=[], session_pairs=pairs,
            )
        return len(queries), pairs, totals

    def test_query_count_is_bounded_as_session_history_grows(self):
        first, _ = self._session(1)
        small_count, _, _ = self._read()
        sessions = [first, *(self._session(order)[0] for order in range(2, 13))]
        large_count, pairs, totals = self._read()
        self.assertEqual(pairs, {(self.enrollment.id, session.id) for session in sessions})
        self.assertEqual([row["session_id"] for row in totals], [session.id for session in reversed(sessions)])
        for row in totals:
            self.assertEqual(row["exam_total"], {
                "score": None, "max_score": 100.0, "selected_count": 1,
                "scored_count": 0, "complete": False,
            })
        self.assertLessEqual(large_count, 20, f"one session={small_count}, twelve={large_count}")
        self.assertLessEqual(large_count, small_count + 1)

    def test_attendance_precedence_and_explicit_targets_apply_across_whole_class(self):
        session, exam = self._session(1)
        Attendance = apps.get_model("attendance", "Attendance")
        attendance = Attendance.objects.create(
            tenant=self.tenant, session=session, enrollment=self.peer, status="PRESENT",
        )
        self.assertEqual(self._read()[1], set())
        attendance.delete()
        assignment = apps.get_model("exams", "ExamEnrollment").objects.create(
            exam=exam, enrollment=self.peer,
        )
        self.assertEqual(self._read()[1], set())
        assignment.delete()
        self.assertEqual(self._read()[1], {(self.enrollment.id, session.id)})

    def test_foreign_inactive_and_unlinked_assessments_do_not_add_pending_totals(self):
        session, exam = self._session(1)
        exam.is_active = False
        exam.save(update_fields=["is_active"])
        other = apps.get_model("core", "Tenant").objects.create(code="totals-foreign", name="Foreign")
        foreign = apps.get_model("exams", "Exam").objects.create(
            tenant=other, title="Foreign", exam_type="regular",
        )
        foreign.sessions.add(session)
        apps.get_model("exams", "Exam").objects.create(tenant=self.tenant, title="Unlinked", exam_type="regular")
        self.assertEqual(self._read()[1], set())
        self.assertEqual(student_exam_session_pairs(tenant=other, enrollment_ids=[self.enrollment.id]), set())

