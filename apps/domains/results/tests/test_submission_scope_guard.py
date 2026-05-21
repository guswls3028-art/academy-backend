from __future__ import annotations

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError as DjangoValidationError
from django.test import TestCase

from apps.core.models import Tenant
from apps.domains.enrollment.models import Enrollment, SessionEnrollment
from apps.domains.exams.models import AnswerKey, Exam, ExamEnrollment, ExamQuestion, Sheet
from apps.domains.lectures.models import Lecture, Session
from apps.domains.results.models import ExamAttempt, ExamResult, Result
from apps.domains.results.services.exam_grading_service import ExamGradingService
from apps.domains.results.services.sync_result_from_submission import (
    sync_result_from_exam_submission,
)
from apps.domains.students.models import Student
from apps.domains.submissions.models import Submission, SubmissionAnswer


User = get_user_model()


class SubmissionScopeGuardTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(code="scope-guard", name="Scope Guard", is_active=True)
        self.admin = User.objects.create_user(
            username="scope-guard-admin",
            password="pw1234",
            tenant=self.tenant,
            is_staff=True,
        )
        self.lecture = Lecture.objects.create(
            tenant=self.tenant,
            title="Scope Guard Lecture",
            name="Scope Guard Lecture",
            subject="MATH",
        )
        self.session = Session.objects.create(lecture=self.lecture, order=1, title="1회")
        self.exam = Exam.objects.create(
            tenant=self.tenant,
            title="Scope Guard Exam",
            exam_type=Exam.ExamType.REGULAR,
            pass_score=0,
            max_score=10,
        )
        self.exam.sessions.add(self.session)
        self.sheet = Sheet.objects.create(exam=self.exam, name="MAIN", total_questions=1)
        self.question = ExamQuestion.objects.create(sheet=self.sheet, number=1, score=10)
        AnswerKey.objects.create(exam=self.exam, answers={str(self.question.id): "1"})

        student_user = User.objects.create_user(
            username="scope-guard-student",
            password="pw1234",
            tenant=self.tenant,
        )
        self.student = Student.objects.create(
            tenant=self.tenant,
            user=student_user,
            name="Scope Student",
            ps_number="SG-1",
            omr_code="SG000001",
        )
        self.enrollment = Enrollment.objects.create(
            tenant=self.tenant,
            lecture=self.lecture,
            student=self.student,
            status="ACTIVE",
        )

    def _unassigned_submission(self):
        submission = Submission.objects.create(
            tenant=self.tenant,
            user=self.admin,
            enrollment_id=self.enrollment.id,
            target_type=Submission.TargetType.EXAM,
            target_id=self.exam.id,
            source=Submission.Source.ONLINE,
            status=Submission.Status.ANSWERS_READY,
        )
        SubmissionAnswer.objects.create(
            tenant=self.tenant,
            submission=submission,
            exam_question_id=self.question.id,
            answer="1",
        )
        return submission

    def test_auto_grade_rejects_unassigned_submission_without_side_effects(self):
        submission = self._unassigned_submission()

        with self.assertRaises(DjangoValidationError):
            ExamGradingService().auto_grade_objective(submission_id=submission.id)

        self.assertFalse(ExamResult.objects.filter(submission=submission).exists())
        self.assertFalse(Result.objects.filter(target_id=self.exam.id, enrollment_id=self.enrollment.id).exists())
        self.assertFalse(ExamAttempt.objects.filter(exam=self.exam, enrollment=self.enrollment).exists())

    def test_sync_rejects_unassigned_submission_without_side_effects(self):
        submission = self._unassigned_submission()

        with self.assertRaises(DjangoValidationError):
            sync_result_from_exam_submission(submission.id)

        self.assertFalse(Result.objects.filter(target_id=self.exam.id, enrollment_id=self.enrollment.id).exists())
        self.assertFalse(ExamAttempt.objects.filter(exam=self.exam, enrollment=self.enrollment).exists())

    def test_session_enrollment_alone_does_not_assign_exam_submission(self):
        SessionEnrollment.objects.create(
            tenant=self.tenant,
            session=self.session,
            enrollment=self.enrollment,
        )
        submission = self._unassigned_submission()

        with self.assertRaises(DjangoValidationError):
            ExamGradingService().auto_grade_objective(submission_id=submission.id)

        self.assertFalse(ExamResult.objects.filter(submission=submission).exists())
        self.assertFalse(Result.objects.filter(target_id=self.exam.id, enrollment_id=self.enrollment.id).exists())

    def test_sync_rejects_duplicate_submission_when_retake_is_disabled(self):
        ExamEnrollment.objects.create(exam=self.exam, enrollment=self.enrollment)
        first = self._unassigned_submission()

        first_result = sync_result_from_exam_submission(first.id)
        self.assertIsNotNone(first_result)
        first.status = Submission.Status.DONE
        first.save(update_fields=["status", "updated_at"])
        second = self._unassigned_submission()

        with self.assertRaises(DjangoValidationError):
            sync_result_from_exam_submission(second.id)

        attempts = ExamAttempt.objects.filter(exam=self.exam, enrollment=self.enrollment)
        self.assertEqual(attempts.count(), 1)
        self.assertEqual(attempts.get().submission_id, first.id)
        result = Result.objects.get(target_id=self.exam.id, enrollment_id=self.enrollment.id)
        self.assertEqual(result.attempt.submission_id, first.id)
        self.assertFalse(ExamResult.objects.filter(submission=second).exists())
