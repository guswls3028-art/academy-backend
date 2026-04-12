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
import pytest
from django.contrib.auth import get_user_model
from django.db import IntegrityError, connection, transaction
from django.test import TransactionTestCase

from apps.core.models import Tenant, TenantMembership
from apps.domains.exams.models import Exam
from apps.domains.lectures.models import Lecture, Session
from apps.domains.enrollment.models import Enrollment
from apps.domains.students.models import Student
from apps.domains.results.models import ExamAttempt

pytestmark = pytest.mark.django_db(transaction=True)
User = get_user_model()


class TestP0ConcurrencyPG(TransactionTestCase):
    """Real PostgreSQL concurrency tests for P0 constraints."""

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
        return tenant, enrollment, exam

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
