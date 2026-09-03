"""
P0 Concurrency Tests — PostgreSQL REQUIRED
Verifies database-level constraints and transaction behavior under concurrent access.
These tests use TransactionTestCase to allow real multi-threaded DB access.

Run:
  DJANGO_SETTINGS_MODULE=apps.api.config.settings.test_pg \
  pytest apps/domains/results/tests/test_p0_concurrency_pg.py -v
"""
from __future__ import annotations

import threading
import unittest
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.db import IntegrityError, connection, transaction
from django.test import TransactionTestCase

from apps.core.models import Tenant
from apps.domains.exams.models import Exam, ExamEnrollment
from apps.domains.lectures.models import Lecture, Session
from apps.domains.enrollment.models import Enrollment
from apps.domains.students.models import Student
from apps.domains.results.models import ExamAttempt
from apps.domains.submissions.models import Submission
from apps.support.student_app.exam_dependencies import (
    StudentExamSubmitError,
    create_online_exam_submission,
)
from apps.support.submissions.dependencies import regrade_exam_submissions

pytestmark = pytest.mark.django_db(transaction=True)
User = get_user_model()


class TestP0ConcurrencyPG(TransactionTestCase):
    """Real PostgreSQL concurrency tests for P0 constraints."""

    @classmethod
    def setUpClass(cls):
        if connection.vendor != "postgresql":
            raise unittest.SkipTest(
                "PostgreSQL database-level concurrency is required for this test."
            )
        super().setUpClass()

    def _setup_data(self):
        tenant = Tenant.objects.create(name="ConcTest", code="conc99", is_active=True)
        user = User.objects.create(
            tenant=tenant, username=f"t{tenant.id}_stu", is_active=True,
        )
        lecture = Lecture.objects.create(
            tenant=tenant, title="ConcLec", name="ConcLec", subject="MATH",
        )
        session = Session.objects.create(lecture=lecture, order=1, title="S1")
        student = Student.objects.create(tenant=tenant, user=user, name="ConcStu")
        enrollment = Enrollment.objects.create(
            tenant=tenant, student=student, lecture=lecture, status="ACTIVE",
        )
        exam = Exam(
            tenant=tenant, title="ConcExam",
            exam_type="REGULAR", max_score=100, pass_score=60, max_attempts=3,
        )
        exam.save()
        exam.sessions.add(session)
        ExamEnrollment.objects.create(exam=exam, enrollment=enrollment)
        return tenant, enrollment, exam

    def test_concurrent_legal_retake_creates_one_active_submission(self):
        tenant, enrollment, exam = self._setup_data()
        exam.allow_retake = True
        exam.max_attempts = 2
        exam.save(update_fields=["allow_retake", "max_attempts", "updated_at"])
        first = Submission.objects.create(
            tenant=tenant,
            user=enrollment.student.user,
            enrollment=enrollment,
            target_type=Submission.TargetType.EXAM,
            target_id=exam.id,
            source=Submission.Source.ONLINE,
            status=Submission.Status.DONE,
        )
        ExamAttempt.objects.create(
            exam=exam,
            enrollment=enrollment,
            submission_id=first.id,
            attempt_index=1,
            status="done",
        )
        barrier = threading.Barrier(2, timeout=10)
        results = {"created": 0, "conflict": 0, "errors": []}

        def submit_retake():
            try:
                connection.close()
                barrier.wait()
                create_online_exam_submission(
                    request_user=enrollment.student.user,
                    request_student=enrollment.student,
                    tenant=tenant,
                    exam=exam,
                    enrollment=enrollment,
                    answers=[{"exam_question_id": 1, "answer": "1"}],
                )
                results["created"] += 1
            except StudentExamSubmitError as exc:
                if exc.status_code == 409:
                    results["conflict"] += 1
                else:
                    results["errors"].append(str(exc))
            except Exception as exc:
                results["errors"].append(f"{type(exc).__name__}: {exc}")
            finally:
                connection.close()

        threads = [threading.Thread(target=submit_retake) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(15)

        self.assertEqual(results, {"created": 1, "conflict": 1, "errors": []})
        first.refresh_from_db()
        self.assertEqual(first.status, Submission.Status.SUPERSEDED)
        self.assertEqual(
            Submission.objects.filter(
                enrollment=enrollment,
                target_type=Submission.TargetType.EXAM,
                target_id=exam.id,
                status=Submission.Status.SUBMITTED,
            ).count(),
            1,
        )

    def test_concurrent_regrade_then_absent_confirmation_finishes_absent(self):
        tenant, enrollment, exam = self._setup_data()
        submission = Submission.objects.create(
            tenant=tenant,
            user=enrollment.student.user,
            enrollment=enrollment,
            target_type=Submission.TargetType.EXAM,
            target_id=exam.id,
            source=Submission.Source.OMR_SCAN,
            status=Submission.Status.DONE,
        )
        attempt = ExamAttempt.objects.create(
            exam=exam,
            enrollment=enrollment,
            submission_id=submission.id,
            attempt_index=1,
            status="done",
            meta={},
        )
        grading_started = threading.Event()
        allow_grading_to_finish = threading.Event()
        absent_finished = threading.Event()
        errors = []

        def fake_grade(_submission_id, *, force_regrade):
            self.assertTrue(force_regrade)
            grading_started.set()
            self.assertTrue(allow_grading_to_finish.wait(10))

        def run_regrade():
            try:
                connection.close()
                regrade_exam_submissions(tenant=tenant, exam_id=exam.id, actor="pg-test")
            except Exception as exc:
                errors.append(f"regrade {type(exc).__name__}: {exc}")
            finally:
                connection.close()

        def confirm_absent():
            try:
                connection.close()
                with transaction.atomic():
                    locked = ExamAttempt.objects.select_for_update().get(id=attempt.id)
                    locked.meta = {**(locked.meta or {}), "status": "NOT_SUBMITTED"}
                    locked.save(update_fields=["meta", "updated_at"])
                absent_finished.set()
            except Exception as exc:
                errors.append(f"absent {type(exc).__name__}: {exc}")
            finally:
                connection.close()

        with patch("apps.support.submissions.dependencies.grade_submission_objective", side_effect=fake_grade):
            regrade_thread = threading.Thread(target=run_regrade)
            absent_thread = threading.Thread(target=confirm_absent)
            regrade_thread.start()
            self.assertTrue(grading_started.wait(10))
            absent_thread.start()
            self.assertFalse(absent_finished.wait(0.2))
            allow_grading_to_finish.set()
            regrade_thread.join(15)
            absent_thread.join(15)

        self.assertEqual(errors, [])
        attempt.refresh_from_db()
        self.assertEqual(attempt.meta.get("status"), "NOT_SUBMITTED")

    def test_concurrent_representative_creation_only_one_wins(self):
        """
        Two threads simultaneously create is_representative=True
        for the same (exam, enrollment). Only one should succeed (DB constraint).
        """
        tenant, enrollment, exam = self._setup_data()

        results = {"success": 0, "integrity_error": 0, "other_error": []}
        barrier = threading.Barrier(2, timeout=10)

        def create_rep(idx):
            try:
                connection.close()
                barrier.wait()
                ExamAttempt.objects.create(
                    exam=exam, enrollment=enrollment,
                    attempt_index=idx, is_representative=True, submission_id=None,
                )
                results["success"] += 1
            except IntegrityError:
                results["integrity_error"] += 1
            except Exception as e:
                results["other_error"].append(f"{type(e).__name__}: {e}")

        t1 = threading.Thread(target=create_rep, args=(1,))
        t2 = threading.Thread(target=create_rep, args=(2,))
        t1.start(); t2.start()
        t1.join(15); t2.join(15)

        self.assertEqual(results["success"], 1, f"Results: {results}")
        self.assertEqual(results["integrity_error"], 1, f"Results: {results}")
        self.assertEqual(len(results["other_error"]), 0, f"Results: {results}")

        rep_count = ExamAttempt.objects.filter(
            exam=exam, enrollment=enrollment, is_representative=True,
        ).count()
        self.assertEqual(rep_count, 1, "DB must have exactly 1 representative")

    def test_concurrent_submission_id_only_one_wins(self):
        """
        Two threads create attempts with same submission_id.
        DB unique constraint blocks the second.
        """
        tenant, enrollment, exam = self._setup_data()

        # Pre-create attempt_index=1 so we can have 2 threads with idx 2,3
        ExamAttempt.objects.create(
            exam=exam, enrollment=enrollment,
            attempt_index=1, is_representative=True, submission_id=None,
        )

        results = {"success": 0, "integrity_error": 0, "other_error": []}
        barrier = threading.Barrier(2, timeout=10)

        def create_sub(idx):
            try:
                connection.close()
                barrier.wait()
                ExamAttempt.objects.create(
                    exam=exam, enrollment=enrollment,
                    attempt_index=idx, is_representative=False,
                    submission_id=12345,  # same submission_id
                )
                results["success"] += 1
            except IntegrityError:
                results["integrity_error"] += 1
            except Exception as e:
                results["other_error"].append(f"{type(e).__name__}: {e}")

        t1 = threading.Thread(target=create_sub, args=(2,))
        t2 = threading.Thread(target=create_sub, args=(3,))
        t1.start(); t2.start()
        t1.join(15); t2.join(15)

        self.assertEqual(results["success"], 1, f"Results: {results}")
        self.assertEqual(results["integrity_error"], 1, f"Results: {results}")

    def test_representative_swap_is_atomic(self):
        """
        Swap representative from attempt 1 to attempt 2.
        After swap, exactly 1 representative exists.
        """
        tenant, enrollment, exam = self._setup_data()

        a1 = ExamAttempt.objects.create(
            exam=exam, enrollment=enrollment,
            attempt_index=1, is_representative=True,
        )

        with transaction.atomic():
            ExamAttempt.objects.filter(id=a1.id).update(is_representative=False)
            a2 = ExamAttempt.objects.create(
                exam=exam, enrollment=enrollment,
                attempt_index=2, is_representative=True,
            )

        a1.refresh_from_db()
        self.assertFalse(a1.is_representative)
        self.assertTrue(a2.is_representative)

        rep_count = ExamAttempt.objects.filter(
            exam=exam, enrollment=enrollment, is_representative=True,
        ).count()
        self.assertEqual(rep_count, 1)

    def test_check_constraint_max_attempts_zero_rejected(self):
        """DB CHECK prevents max_attempts=0."""
        tenant, enrollment, exam = self._setup_data()
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Exam.objects.create(
                    tenant=tenant, title="Bad",
                    exam_type="REGULAR", max_score=100, pass_score=60, max_attempts=0,
                )

    def test_check_constraint_pass_exceeds_max_rejected(self):
        """DB CHECK prevents pass_score > max_score."""
        tenant, enrollment, exam = self._setup_data()
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Exam.objects.create(
                    tenant=tenant, title="Bad2",
                    exam_type="REGULAR", max_score=100, pass_score=150, max_attempts=1,
                )

    def test_null_submission_id_allows_multiple(self):
        """submission_id=NULL should NOT trigger unique constraint (PG partial index)."""
        tenant, enrollment, exam = self._setup_data()

        ExamAttempt.objects.create(
            exam=exam, enrollment=enrollment,
            attempt_index=1, is_representative=True, submission_id=None,
        )
        # Second attempt with NULL submission_id should succeed
        a2 = ExamAttempt.objects.create(
            exam=exam, enrollment=enrollment,
            attempt_index=2, is_representative=False, submission_id=None,
        )
        self.assertIsNotNone(a2.id)
