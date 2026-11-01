"""Real PostgreSQL races around legacy OMR roster creation and callback locks."""

from __future__ import annotations

import threading
import unittest

from django.contrib.auth import get_user_model
from django.db import close_old_connections, connection, transaction
from django.test import TransactionTestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.models import Tenant, TenantMembership
from apps.domains.enrollment.models import Enrollment, SessionEnrollment
from apps.domains.exams.models import AnswerKey, Exam, ExamEnrollment, ExamQuestion, Sheet
from apps.domains.exams.views.exam_enrollment_view import ExamEnrollmentManageView
from apps.domains.lectures.models import Lecture, Session
from apps.domains.students.models import Student
from apps.domains.submissions.models import Submission
from apps.domains.submissions.services.ai_omr_result_mapper import apply_omr_ai_result
from apps.support.omr.candidate_matching import ensure_exam_enrollment_candidate


class OmrRosterConcurrencyPostgresTests(TransactionTestCase):
    @classmethod
    def setUpClass(cls):
        if connection.vendor != "postgresql":
            raise unittest.SkipTest("PostgreSQL row locks are required for OMR roster races")
        super().setUpClass()

    def setUp(self):
        self.tenant = Tenant.objects.create(name="OMR roster race", code="omr-roster-race")
        self.teacher = get_user_model().objects.create_user(
            username="omr-roster-teacher", tenant=self.tenant, is_staff=True,
        )
        TenantMembership.ensure_active(tenant=self.tenant, user=self.teacher, role="teacher")
        lecture = Lecture.objects.create(tenant=self.tenant, title="OMR", name="OMR", subject="MATH")
        self.session = Session.objects.create(lecture=lecture, order=1, title="OMR session")
        self.exam = Exam.objects.create(
            tenant=self.tenant, title="OMR exam", exam_type=Exam.ExamType.REGULAR, max_score=100,
        )
        self.exam.sessions.add(self.session)
        self.enrollments = []
        for index, code in enumerate(("12345678", "99887766")):
            user = get_user_model().objects.create_user(username=f"omr-roster-student-{index}", tenant=self.tenant)
            student = Student.objects.create(
                tenant=self.tenant, user=user, name=f"OMR student {index}",
                ps_number=f"omr-roster-{index}", phone=f"010{code}", omr_code=code,
            )
            enrollment = Enrollment.objects.create(
                tenant=self.tenant, student=student, lecture=lecture, status="ACTIVE",
            )
            SessionEnrollment.objects.create(tenant=self.tenant, session=self.session, enrollment=enrollment)
            self.enrollments.append(enrollment)
        sheet = Sheet.objects.create(exam=self.exam, name="OMR", total_questions=1)
        question = ExamQuestion.objects.create(sheet=sheet, number=1, score=100)
        AnswerKey.objects.create(exam=self.exam, answers={str(question.id): "1"})
        self.errors = []
        self.values = {}

    def _thread(self, name, work):
        def run():
            close_old_connections()
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SET statement_timeout = '8s'")
                    cursor.execute("SET lock_timeout = '6s'")
                self.values[name] = work()
            except BaseException as exc:
                self.errors.append(exc)
            finally:
                connection.close()

        worker = threading.Thread(target=run, name=name)
        worker.start()
        return worker

    def _join(self, *workers):
        for worker in workers:
            worker.join(timeout=12)
            self.assertFalse(worker.is_alive(), f"{worker.name} did not finish")
        self.assertEqual(self.errors, [])

    def _ensure(self, enrollment):
        return ensure_exam_enrollment_candidate(
            tenant=self.tenant, exam_id=self.exam.id, enrollment_id=enrollment.id,
        )

    def test_two_first_receipts_preserve_the_entire_eligible_roster(self):
        ready = threading.Barrier(2, timeout=8)

        def first_receipt(enrollment):
            ready.wait()
            return self._ensure(enrollment)

        workers = [self._thread(str(index), lambda e=e: first_receipt(e)) for index, e in enumerate(self.enrollments)]
        self._join(*workers)
        self.assertEqual(self.values, {"0": True, "1": True})
        self.assertEqual(set(ExamEnrollment.objects.filter(exam=self.exam).values_list(
            "enrollment_id", flat=True,
        )), {e.id for e in self.enrollments})

    def test_roster_put_wins_over_waiting_legacy_candidate_without_expanding_subset(self):
        started = threading.Event()
        finished = threading.Event()

        def waiting_receipt():
            started.set()
            try:
                return self._ensure(self.enrollments[1])
            finally:
                finished.set()

        worker = None
        try:
            with transaction.atomic():
                Exam.objects.select_for_update().get(pk=self.exam.pk)
                worker = self._thread("waiting-receipt", waiting_receipt)
                self.assertTrue(started.wait(timeout=5))
                self.assertFalse(finished.wait(timeout=0.1))
                request = APIRequestFactory().put("/exams/enrollments/", {
                    "session_id": self.session.id, "enrollment_ids": [self.enrollments[0].id],
                }, format="json")
                request.tenant = self.tenant
                force_authenticate(request, user=self.teacher)
                response = ExamEnrollmentManageView.as_view()(request, exam_id=self.exam.id)
                self.assertEqual(response.status_code, 200, response.data)
        finally:
            if worker:
                self._join(worker)
        self.assertFalse(self.values["waiting-receipt"])
        self.assertEqual(list(ExamEnrollment.objects.filter(exam=self.exam).values_list(
            "enrollment_id", flat=True,
        )), [self.enrollments[0].id])

    def test_callback_waits_for_exam_before_locking_submission(self):
        submission = Submission.objects.create(
            tenant=self.tenant, user=self.teacher, target_type="exam", target_id=self.exam.id,
            source=Submission.Source.OMR_SCAN, status=Submission.Status.DISPATCHED,
        )
        at_exam_lock = threading.Event()

        def callback():
            def observe(execute, sql, params, many, context):
                if '"exams_exam"' in sql and ("FOR UPDATE" in sql or "FOR NO KEY UPDATE" in sql):
                    at_exam_lock.set()
                return execute(sql, params, many, context)

            with connection.execute_wrapper(observe):
                return apply_omr_ai_result({
                    "submission_id": submission.id, "tenant_id": self.tenant.id,
                    "status": "DONE", "version": "v15", "aligned": True,
                    "identifier": {"status": "ok", "identifier": "12345678"},
                    "answers": [{"question_id": 1, "detected": ["1"], "status": "ok", "confidence": 0.99}],
                })

        worker = None
        try:
            with transaction.atomic():
                Exam.objects.select_for_update(no_key=True).get(pk=self.exam.pk)
                worker = self._thread("callback", callback)
                self.assertTrue(at_exam_lock.wait(timeout=5))
                # A submission-first callback would hold this row while waiting
                # for our exam, recreating the grading/callback deadlock.
                Submission.objects.select_for_update(nowait=True).get(pk=submission.pk)
        finally:
            if worker:
                self._join(worker)
        submission.refresh_from_db()
        self.assertEqual(self.values["callback"], submission.id)
        self.assertEqual(submission.status, Submission.Status.ANSWERS_READY)
        self.assertEqual(submission.enrollment_id, self.enrollments[0].id)
