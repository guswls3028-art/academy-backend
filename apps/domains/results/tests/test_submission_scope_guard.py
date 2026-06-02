from __future__ import annotations

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError as DjangoValidationError
from django.test import TestCase

from apps.core.models import Tenant
from apps.domains.enrollment.models import Enrollment, SessionEnrollment
from apps.domains.exams.models import AnswerKey, Exam, ExamEnrollment, ExamQuestion, Sheet
from apps.domains.lectures.models import Lecture, Session
from apps.domains.results.models import ExamAttempt, ExamResult, Result, ResultItem
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

    def _submission_for_exam(self, exam, question, answer="1"):
        submission = Submission.objects.create(
            tenant=self.tenant,
            user=self.admin,
            enrollment_id=self.enrollment.id,
            target_type=Submission.TargetType.EXAM,
            target_id=exam.id,
            source=Submission.Source.ONLINE,
            status=Submission.Status.ANSWERS_READY,
        )
        SubmissionAnswer.objects.create(
            tenant=self.tenant,
            submission=submission,
            exam_question_id=question.id,
            answer=answer,
        )
        return submission

    def _create_mixed_exam(self):
        exam = Exam.objects.create(
            tenant=self.tenant,
            title="Mixed Scope Guard Exam",
            exam_type=Exam.ExamType.REGULAR,
            pass_score=0,
            max_score=100,
        )
        exam.sessions.add(self.session)
        ExamEnrollment.objects.create(exam=exam, enrollment=self.enrollment)
        sheet = Sheet.objects.create(
            exam=exam,
            name="MIXED",
            total_questions=2,
            choice_count=1,
            essay_count=1,
        )
        choice = ExamQuestion.objects.create(sheet=sheet, number=1, score=80)
        essay = ExamQuestion.objects.create(sheet=sheet, number=2, score=20)
        AnswerKey.objects.create(exam=exam, answers={str(choice.id): "1"})
        return exam, choice, essay

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

    def test_sync_attaches_zero_manual_placeholder_to_real_submission(self):
        ExamEnrollment.objects.create(exam=self.exam, enrollment=self.enrollment)
        attempt = ExamAttempt.objects.create(
            exam=self.exam,
            enrollment=self.enrollment,
            submission_id=0,
            attempt_index=1,
            is_representative=True,
            status="done",
            meta={
                "initial_snapshot": {
                    "total_score": 0.0,
                    "max_score": 10.0,
                    "source": "admin_manual_total",
                }
            },
        )
        Result.objects.create(
            target_type="exam",
            target_id=self.exam.id,
            enrollment=self.enrollment,
            attempt=attempt,
            total_score=0.0,
            max_score=10.0,
            objective_score=0.0,
        )
        submission = self._unassigned_submission()

        result = sync_result_from_exam_submission(submission.id)

        attempt.refresh_from_db()
        self.assertEqual(attempt.submission_id, submission.id)
        self.assertEqual(attempt.attempt_index, 1)
        self.assertTrue(attempt.is_representative)
        self.assertEqual(attempt.meta["initial_snapshot"]["source"], "omr_replaced_manual_zero")
        self.assertEqual(float(result.total_score), 10.0)
        self.assertEqual(float(result.objective_score), 10.0)
        self.assertEqual(ExamAttempt.objects.filter(exam=self.exam, enrollment=self.enrollment).count(), 1)

    def test_sync_attaches_zero_manual_component_placeholders_to_real_submission(self):
        ExamEnrollment.objects.create(exam=self.exam, enrollment=self.enrollment)

        for source in ("admin_manual_objective", "admin_manual_subjective"):
            with self.subTest(source=source):
                ExamAttempt.objects.filter(exam=self.exam, enrollment=self.enrollment).delete()
                Result.objects.filter(target_id=self.exam.id, enrollment_id=self.enrollment.id).delete()
                Submission.objects.filter(
                    tenant=self.tenant,
                    user=self.admin,
                    target_type=Submission.TargetType.EXAM,
                    target_id=self.exam.id,
                ).delete()
                submission = self._unassigned_submission()
                attempt = ExamAttempt.objects.create(
                    exam=self.exam,
                    enrollment=self.enrollment,
                    submission_id=0,
                    attempt_index=1,
                    is_representative=True,
                    status="done",
                    meta={
                        "initial_snapshot": {
                            "total_score": 0.0,
                            "max_score": 10.0,
                            "source": source,
                        }
                    },
                )
                Result.objects.create(
                    target_type="exam",
                    target_id=self.exam.id,
                    enrollment=self.enrollment,
                    attempt=attempt,
                    total_score=0.0,
                    max_score=10.0,
                    objective_score=0.0,
                )

                result = sync_result_from_exam_submission(submission.id)

                attempt.refresh_from_db()
                self.assertEqual(attempt.submission_id, submission.id)
                self.assertEqual(attempt.meta["initial_snapshot"]["source"], "omr_replaced_manual_zero")
                self.assertEqual(float(result.total_score), 10.0)
                self.assertEqual(float(result.objective_score), 10.0)

    def test_sync_does_not_overwrite_nonzero_manual_placeholder(self):
        ExamEnrollment.objects.create(exam=self.exam, enrollment=self.enrollment)
        attempt = ExamAttempt.objects.create(
            exam=self.exam,
            enrollment=self.enrollment,
            submission_id=0,
            attempt_index=1,
            is_representative=True,
            status="done",
            meta={
                "initial_snapshot": {
                    "total_score": 7.0,
                    "max_score": 10.0,
                    "source": "admin_manual_total",
                }
            },
        )
        Result.objects.create(
            target_type="exam",
            target_id=self.exam.id,
            enrollment=self.enrollment,
            attempt=attempt,
            total_score=7.0,
            max_score=10.0,
            objective_score=7.0,
        )
        submission = self._unassigned_submission()

        with self.assertRaises(DjangoValidationError):
            sync_result_from_exam_submission(submission.id)

        attempt.refresh_from_db()
        self.assertEqual(attempt.submission_id, 0)
        result = Result.objects.get(target_id=self.exam.id, enrollment_id=self.enrollment.id)
        self.assertEqual(float(result.total_score), 7.0)
        self.assertFalse(ExamResult.objects.filter(submission=submission).exists())

    def test_sync_combines_omr_objective_with_existing_manual_subjective_score(self):
        exam, choice, _essay = self._create_mixed_exam()
        attempt = ExamAttempt.objects.create(
            exam=exam,
            enrollment=self.enrollment,
            submission_id=0,
            attempt_index=1,
            is_representative=True,
            status="done",
            meta={
                "initial_snapshot": {
                    "total_score": 15.0,
                    "max_score": 100.0,
                    "source": "admin_manual_subjective",
                }
            },
        )
        Result.objects.create(
            target_type="exam",
            target_id=exam.id,
            enrollment=self.enrollment,
            attempt=attempt,
            total_score=15.0,
            max_score=100.0,
            objective_score=0.0,
        )
        submission = self._submission_for_exam(exam, choice, answer="1")

        result = sync_result_from_exam_submission(submission.id)

        attempt.refresh_from_db()
        self.assertEqual(attempt.submission_id, submission.id)
        self.assertEqual(attempt.meta["initial_snapshot"]["source"], "omr_attached_manual_subjective")
        self.assertEqual(float(result.objective_score), 80.0)
        self.assertEqual(float(result.total_score), 95.0)
        self.assertEqual(float(result.max_score), 100.0)

    def test_sync_preserves_manual_subjective_score_and_skips_essay_result_items(self):
        exam, choice, essay = self._create_mixed_exam()
        submission = self._submission_for_exam(exam, choice, answer="1")

        result = sync_result_from_exam_submission(submission.id)
        self.assertEqual(float(result.objective_score), 80.0)
        self.assertEqual(float(result.total_score), 80.0)
        self.assertEqual(float(result.max_score), 100.0)
        self.assertTrue(ResultItem.objects.filter(result=result, question=choice).exists())
        self.assertFalse(ResultItem.objects.filter(result=result, question=essay).exists())

        ResultItem.objects.create(
            result=result,
            question=essay,
            answer="manual",
            is_correct=True,
            score=15.0,
            max_score=20.0,
            source="manual",
        )
        result.total_score = 95.0
        result.objective_score = 80.0
        result.max_score = 100.0
        result.save(update_fields=["total_score", "objective_score", "max_score", "updated_at"])

        result = sync_result_from_exam_submission(submission.id)

        self.assertEqual(float(result.objective_score), 80.0)
        self.assertEqual(float(result.total_score), 95.0)
        self.assertEqual(float(result.max_score), 100.0)
        self.assertFalse(
            ResultItem.objects.filter(
                result=result,
                question=essay,
                source__in=["online", "omr"],
            ).exists()
        )
        self.assertTrue(ResultItem.objects.filter(result=result, question=essay, source="manual").exists())

    def test_sync_combines_omr_with_existing_manual_essay_item_score(self):
        exam, choice, essay = self._create_mixed_exam()
        attempt = ExamAttempt.objects.create(
            exam=exam,
            enrollment=self.enrollment,
            submission_id=0,
            attempt_index=1,
            is_representative=True,
            status="done",
            meta={"source": "manual_entry"},
        )
        result = Result.objects.create(
            target_type="exam",
            target_id=exam.id,
            enrollment=self.enrollment,
            attempt=attempt,
            total_score=15.0,
            max_score=100.0,
            objective_score=0.0,
        )
        ResultItem.objects.create(
            result=result,
            question=essay,
            answer="manual",
            is_correct=True,
            score=15.0,
            max_score=20.0,
            source="manual",
        )
        submission = self._submission_for_exam(exam, choice, answer="1")

        result = sync_result_from_exam_submission(submission.id)

        attempt.refresh_from_db()
        self.assertEqual(attempt.submission_id, submission.id)
        self.assertEqual(attempt.meta["initial_snapshot"]["source"], "omr_attached_manual_essay_items")
        self.assertEqual(float(result.objective_score), 80.0)
        self.assertEqual(float(result.total_score), 95.0)
        self.assertEqual(float(result.max_score), 100.0)
        self.assertEqual(ResultItem.objects.filter(result=result).count(), 2)
        self.assertTrue(ResultItem.objects.filter(result=result, question=essay, source="manual").exists())
        self.assertTrue(ResultItem.objects.filter(result=result, question=choice, source="online").exists())
        self.assertFalse(
            ResultItem.objects.filter(
                result=result,
                question=essay,
                source__in=["online", "omr"],
            ).exists()
        )

    def test_sync_does_not_attach_manual_entry_with_objective_item_score(self):
        exam, choice, _essay = self._create_mixed_exam()
        attempt = ExamAttempt.objects.create(
            exam=exam,
            enrollment=self.enrollment,
            submission_id=0,
            attempt_index=1,
            is_representative=True,
            status="done",
            meta={"source": "manual_entry"},
        )
        result = Result.objects.create(
            target_type="exam",
            target_id=exam.id,
            enrollment=self.enrollment,
            attempt=attempt,
            total_score=80.0,
            max_score=100.0,
            objective_score=80.0,
        )
        ResultItem.objects.create(
            result=result,
            question=choice,
            answer="1",
            is_correct=True,
            score=80.0,
            max_score=80.0,
            source="manual",
        )
        submission = self._submission_for_exam(exam, choice, answer="1")

        with self.assertRaises(DjangoValidationError):
            sync_result_from_exam_submission(submission.id)

        attempt.refresh_from_db()
        result.refresh_from_db()
        self.assertEqual(attempt.submission_id, 0)
        self.assertEqual(float(result.total_score), 80.0)
        self.assertEqual(float(result.objective_score), 80.0)
