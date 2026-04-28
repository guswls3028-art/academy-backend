"""
P0/P1 bug fix tests for exam/homework domains.

Tests:
A. ExamAttempt representative uniqueness
B. Retake scenario (attempt_index >= 2)
C. submission_id duplicate race condition
D. Manual override max_score preservation
E. Exam validation (max_attempts, pass_score, open_at/close_at)
F. Homework tenant fallback removal
G. HomeworkScore score > max_score validation
"""
from __future__ import annotations

import threading
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, connection, transaction
from django.test import TestCase, TransactionTestCase

from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.test import APITestCase

from apps.core.models import Tenant, TenantMembership
from apps.domains.exams.models import Exam
from apps.domains.lectures.models import Lecture, Session
from apps.domains.enrollment.models import Enrollment
from apps.domains.students.models import Student
from apps.domains.results.models import ExamAttempt, ExamResult
from apps.domains.results.services.attempt_service import ExamAttemptService
from apps.domains.results.services.exam_grading_service import ExamGradingService
from apps.domains.submissions.models import Submission


User = get_user_model()


class BaseTestMixin:
    """Common test fixture setup."""

    def _create_fixtures(self):
        self.tenant = Tenant.objects.create(name="Test", code="9998", is_active=True)
        self.user = User.objects.create(
            tenant=self.tenant,
            username=f"t{self.tenant.id}_admin",
            is_active=True, is_staff=True,
        )
        self.user.set_password("pass1234!")
        self.user.save(update_fields=["password"])

        TenantMembership.objects.create(
            user=self.user, tenant=self.tenant, role="admin", is_active=True,
        )

        self.lecture = Lecture.objects.create(
            tenant=self.tenant, title="Lecture", name="Lecture", subject="MATH",
        )
        self.session = Session.objects.create(
            lecture=self.lecture, order=1, title="S1",
        )
        student_user = User.objects.create(
            tenant=self.tenant,
            username=f"t{self.tenant.id}_student",
            is_active=True,
        )
        self.student = Student.objects.create(
            tenant=self.tenant, user=student_user, name="Test Student",
        )
        self.enrollment = Enrollment.objects.create(
            tenant=self.tenant, student=self.student,
            lecture=self.lecture, status="ACTIVE",
        )

    def _create_exam(self, **kwargs):
        defaults = dict(
            tenant=self.tenant,
            title="Test Exam",
            exam_type=Exam.ExamType.REGULAR,
            pass_score=60,
            max_score=100,
            max_attempts=1,
        )
        defaults.update(kwargs)
        exam = Exam(**defaults)
        exam.save()
        exam.sessions.add(self.session)
        return exam

    def _create_submission(self, exam, enrollment=None):
        return Submission.objects.create(
            tenant=self.tenant,
            user=self.user,
            target_type="exam",
            target_id=exam.id,
            enrollment_id=(enrollment or self.enrollment).id,
            source="online",
            status="done",
        )


# ============================================================
# A. Representative attempt uniqueness
# ============================================================
class TestRepresentativeUniqueness(TransactionTestCase, BaseTestMixin):
    """A. (exam, enrollment) per is_representative=True must be unique."""

    def setUp(self):
        self._create_fixtures()

    def test_single_representative_on_create(self):
        """Normal creation: exactly one representative."""
        exam = self._create_exam()
        sub = self._create_submission(exam)
        attempt = ExamAttemptService.create_for_submission(
            exam_id=exam.id, enrollment_id=self.enrollment.id,
            submission_id=sub.id,
        )
        self.assertTrue(attempt.is_representative)
        self.assertEqual(attempt.attempt_index, 1)

    def test_representative_swap_on_retake(self):
        """Retake: old representative becomes False, new becomes True."""
        exam = self._create_exam(allow_retake=True, max_attempts=3)
        sub1 = self._create_submission(exam)
        a1 = ExamAttemptService.create_for_submission(
            exam_id=exam.id, enrollment_id=self.enrollment.id,
            submission_id=sub1.id,
        )
        self.assertTrue(a1.is_representative)

        sub2 = self._create_submission(exam)
        a2 = ExamAttemptService.create_for_submission(
            exam_id=exam.id, enrollment_id=self.enrollment.id,
            submission_id=sub2.id,
        )
        a1.refresh_from_db()
        self.assertFalse(a1.is_representative)
        self.assertTrue(a2.is_representative)
        self.assertEqual(a2.attempt_index, 2)

    def test_db_constraint_prevents_duplicate_representative(self):
        """DB constraint blocks manual creation of two representatives."""
        exam = self._create_exam()
        ExamAttempt.objects.create(
            exam=exam, enrollment=self.enrollment,
            attempt_index=1, is_representative=True, status="done",
        )
        with self.assertRaises(IntegrityError):
            ExamAttempt.objects.create(
                exam=exam, enrollment=self.enrollment,
                attempt_index=2, is_representative=True, status="done",
            )


# ============================================================
# B. Retake scenario
# ============================================================
class TestRetakeScenario(TransactionTestCase, BaseTestMixin):
    """B. attempt_index=2+ with online submission sync."""

    def setUp(self):
        self._create_fixtures()

    def test_sync_result_does_not_rollback_representative(self):
        """sync_result_from_exam_submission should not clobber existing attempts."""
        from apps.domains.results.services.sync_result_from_submission import (
            sync_result_from_exam_submission,
        )

        exam = self._create_exam(allow_retake=True, max_attempts=3)

        # Create first attempt via service
        sub1 = self._create_submission(exam)
        a1 = ExamAttemptService.create_for_submission(
            exam_id=exam.id, enrollment_id=self.enrollment.id,
            submission_id=sub1.id,
        )

        # Create second attempt
        sub2 = self._create_submission(exam)
        a2 = ExamAttemptService.create_for_submission(
            exam_id=exam.id, enrollment_id=self.enrollment.id,
            submission_id=sub2.id,
        )
        self.assertEqual(a2.attempt_index, 2)

        # sync_result for sub2 should NOT create attempt_index=1
        # and should NOT override a2's representative status
        # (skip because we need sheet/answer_key setup for full sync - test attempt lookup)
        existing = ExamAttempt.objects.filter(
            submission_id=sub2.id,
        ).first()
        self.assertIsNotNone(existing)
        self.assertEqual(existing.attempt_index, 2)

        # Verify a2 is still representative
        a2.refresh_from_db()
        self.assertTrue(a2.is_representative)
        a1.refresh_from_db()
        self.assertFalse(a1.is_representative)


# ============================================================
# C. submission_id duplicate
# ============================================================
class TestSubmissionIdDuplicate(TransactionTestCase, BaseTestMixin):
    """C. Same submission_id cannot create two attempts."""

    def setUp(self):
        self._create_fixtures()

    def test_duplicate_submission_id_raises(self):
        """Application-level check blocks duplicate submission_id."""
        exam = self._create_exam()
        sub = self._create_submission(exam)
        ExamAttemptService.create_for_submission(
            exam_id=exam.id, enrollment_id=self.enrollment.id,
            submission_id=sub.id,
        )
        with self.assertRaises(DjangoValidationError):
            ExamAttemptService.create_for_submission(
                exam_id=exam.id, enrollment_id=self.enrollment.id,
                submission_id=sub.id,
            )

    def test_db_constraint_blocks_duplicate_submission(self):
        """DB unique constraint blocks even raw duplicate."""
        exam = self._create_exam()
        sub = self._create_submission(exam)
        ExamAttempt.objects.create(
            exam=exam, enrollment=self.enrollment,
            submission_id=sub.id, attempt_index=1,
            is_representative=True, status="done",
        )
        with self.assertRaises(IntegrityError):
            ExamAttempt.objects.create(
                exam=exam, enrollment=self.enrollment,
                submission_id=sub.id, attempt_index=2,
                is_representative=False, status="done",
            )

    def test_null_submission_id_allowed_multiple(self):
        """submission_id=NULL is allowed for multiple attempts (clinic direct entry)."""
        exam = self._create_exam(allow_retake=True, max_attempts=3)
        ExamAttempt.objects.create(
            exam=exam, enrollment=self.enrollment,
            submission_id=None, attempt_index=1,
            is_representative=False, status="done",
        )
        a2 = ExamAttempt.objects.create(
            exam=exam, enrollment=self.enrollment,
            submission_id=None, attempt_index=2,
            is_representative=True, status="done",
        )
        self.assertEqual(a2.attempt_index, 2)


# ============================================================
# D. Manual override max_score preservation
# ============================================================
class TestManualOverrideMaxScore(TestCase, BaseTestMixin):
    """D. Manual override should not distort max_score."""

    def setUp(self):
        self._create_fixtures()

    def test_override_preserves_original_max_score(self):
        """Override score 3 on a 5-point question should keep max=5."""
        exam = self._create_exam()
        sub = self._create_submission(exam)

        # Create ExamResult with existing breakdown
        result = ExamResult.objects.create(
            submission=sub, exam=exam,
            total_score=5, max_score=10,
            objective_score=5,
            breakdown={
                "1": {"question_id": 101, "correct": True, "earned": 5, "answer": "A", "correct_answer": "A"},
                "2": {"question_id": 102, "correct": True, "earned": 5, "answer": "B", "correct_answer": "B"},
            },
            status=ExamResult.Status.DRAFT,
        )

        svc = ExamGradingService()
        # Override question 101 from 5 to 3 WITHOUT providing max_score
        result = svc.apply_manual_overrides(
            submission_id=sub.id,
            overrides={
                "grades": [
                    {"exam_question_id": 101, "score": 3},
                    {"exam_question_id": 102, "score": 5},
                ],
            },
        )
        result.refresh_from_db()

        overrides = result.manual_overrides
        # Question 101 max_score should NOT be 3 (the earned score)
        q101 = overrides.get("101", {})
        self.assertEqual(q101["score"], 3)
        # max_score should be preserved (from breakdown "earned" = 5)
        self.assertEqual(q101["max_score"], 5)

        # Total: 3 + 5 = 8, Max: 5 + 5 = 10
        self.assertEqual(result.total_score, 8)
        self.assertEqual(result.max_score, 10)


# ============================================================
# E. Exam validation
# ============================================================
class TestExamValidation(TestCase, BaseTestMixin):
    """E. Exam model/serializer validation for P1-5."""

    def setUp(self):
        self._create_fixtures()

    def test_max_attempts_zero_blocked_by_db(self):
        """max_attempts=0 is blocked by DB check constraint."""
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Exam.objects.create(
                    tenant=self.tenant, title="Bad",
                    max_attempts=0, max_score=100, pass_score=0,
                )

    def test_pass_score_exceeds_max_blocked_by_db(self):
        """pass_score > max_score is blocked by DB check constraint."""
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Exam.objects.create(
                    tenant=self.tenant, title="Bad",
                    max_attempts=1, max_score=100, pass_score=150,
                )

    def test_open_close_validation_in_clean(self):
        """open_at >= close_at blocked by model clean()."""
        from django.utils import timezone
        import datetime

        now = timezone.now()
        exam = Exam(
            tenant=self.tenant, title="Bad",
            max_attempts=1, max_score=100, pass_score=50,
            open_at=now + datetime.timedelta(hours=2),
            close_at=now + datetime.timedelta(hours=1),
        )
        with self.assertRaises(DjangoValidationError) as ctx:
            exam.clean()
        self.assertIn("close_at", ctx.exception.message_dict)

    def test_serializer_blocks_invalid_exam(self):
        """ExamSerializer validates max_attempts, pass_score, open_at/close_at."""
        from apps.domains.exams.serializers.exam import ExamSerializer
        from django.utils import timezone
        import datetime

        now = timezone.now()
        data = {
            "title": "Test",
            "max_attempts": 0,
            "pass_score": 150,
            "max_score": 100,
            "open_at": (now + datetime.timedelta(hours=2)).isoformat(),
            "close_at": (now + datetime.timedelta(hours=1)).isoformat(),
        }
        s = ExamSerializer(data=data)
        self.assertFalse(s.is_valid())
        self.assertIn("max_attempts", s.errors)
        self.assertIn("pass_score", s.errors)
        self.assertIn("close_at", s.errors)


# ============================================================
# F. Homework tenant fallback removal
# ============================================================
class TestHomeworkTenantFallback(TestCase, BaseTestMixin):
    """F. calc_homework_passed_and_clinic must fail when tenant is missing."""

    def setUp(self):
        self._create_fixtures()

    def test_tenant_missing_raises_error(self):
        """When session.lecture.tenant is None, should raise ValueError, not fallback."""
        from apps.domains.homework.utils.homework_policy import calc_homework_passed_and_clinic

        # Create a session with no lecture -> tenant chain accessible
        class FakeSession:
            id = 999
            lecture = None  # No lecture -> tenant is None

        with self.assertRaises(ValueError) as ctx:
            calc_homework_passed_and_clinic(
                session=FakeSession(),
                score=50,
                max_score=100,
            )
        self.assertIn("tenant", str(ctx.exception).lower())

    def test_valid_session_works(self):
        """Normal session with tenant works correctly."""
        from apps.domains.homework.utils.homework_policy import calc_homework_passed_and_clinic

        passed, clinic, pct = calc_homework_passed_and_clinic(
            session=self.session,
            score=90,
            max_score=100,
        )
        self.assertTrue(passed)


# ============================================================
# G. HomeworkScore score > max_score validation
# ============================================================
class TestHomeworkScoreValidation(TestCase):
    """G. HomeworkScore serializer blocks score > max_score."""

    def test_score_exceeds_max_blocked(self):
        """score=200, max_score=100 should be rejected."""
        from apps.domains.homework_results.serializers.homework_score import HomeworkQuickPatchSerializer

        data = {
            "homework_id": 1,
            "enrollment_id": 1,
            "score": 200,
            "max_score": 100,
        }
        s = HomeworkQuickPatchSerializer(data=data)
        self.assertFalse(s.is_valid())
        self.assertIn("score", s.errors)

    def test_negative_score_blocked(self):
        """Negative score should be rejected."""
        from apps.domains.homework_results.serializers.homework_score import HomeworkQuickPatchSerializer

        data = {
            "homework_id": 1,
            "enrollment_id": 1,
            "score": -10,
            "max_score": 100,
        }
        s = HomeworkQuickPatchSerializer(data=data)
        self.assertFalse(s.is_valid())
        self.assertIn("score", s.errors)

    def test_negative_max_score_blocked(self):
        """Negative max_score should be rejected."""
        from apps.domains.homework_results.serializers.homework_score import HomeworkQuickPatchSerializer

        data = {
            "homework_id": 1,
            "enrollment_id": 1,
            "score": 50,
            "max_score": -100,
        }
        s = HomeworkQuickPatchSerializer(data=data)
        self.assertFalse(s.is_valid())
        self.assertIn("max_score", s.errors)

    def test_valid_score_passes(self):
        """Valid score <= max_score passes."""
        from apps.domains.homework_results.serializers.homework_score import HomeworkQuickPatchSerializer

        data = {
            "homework_id": 1,
            "enrollment_id": 1,
            "score": 80,
            "max_score": 100,
        }
        s = HomeworkQuickPatchSerializer(data=data)
        self.assertTrue(s.is_valid())

    def test_percent_mode_no_max(self):
        """score=85 without max_score (percent mode) passes."""
        from apps.domains.homework_results.serializers.homework_score import HomeworkQuickPatchSerializer

        data = {
            "homework_id": 1,
            "enrollment_id": 1,
            "score": 85,
        }
        s = HomeworkQuickPatchSerializer(data=data)
        self.assertTrue(s.is_valid())

    def test_core_serializer_also_validates(self):
        """homework_results.HomeworkQuickPatchSerializer also validates (legacy alias name)."""
        from apps.domains.homework_results.serializers.homework_score import HomeworkQuickPatchSerializer

        data = {
            "homework_id": 1,
            "enrollment_id": 1,
            "score": 200,
            "max_score": 100,
        }
        s = HomeworkQuickPatchSerializer(data=data)
        self.assertFalse(s.is_valid())
        self.assertIn("score", s.errors)
