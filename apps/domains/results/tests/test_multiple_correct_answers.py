from __future__ import annotations

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase

from apps.core.models import Tenant
from apps.domains.enrollment.models import Enrollment
from apps.domains.exams.models import AnswerKey, Exam, ExamEnrollment, ExamQuestion, Sheet
from apps.domains.lectures.models import Lecture
from apps.domains.results.models import ExamResult, Result, ResultItem
from apps.domains.results.services.answer_matching import (
    answer_matches,
    format_answer_for_display,
)
from apps.domains.results.services.grading_service import grade_submission
from apps.domains.students.models import Student
from apps.domains.submissions.models import Submission, SubmissionAnswer
from apps.support.omr.score_adjustment import SCORE_ADJUSTMENT_KEY
from apps.support.omr.score_shape import get_exam_score_shape


User = get_user_model()


class AnswerMatchingTests(SimpleTestCase):
    def test_pipe_objective_candidates_match_any_choice(self):
        self.assertTrue(answer_matches("3", "1|3"))
        self.assertTrue(answer_matches("4", "2|4"))
        self.assertTrue(answer_matches("2", "① 또는 ②"))
        self.assertEqual(format_answer_for_display(["1", "3"]), "1,3")

    def test_delimited_objective_candidates_require_same_mark_set(self):
        self.assertTrue(answer_matches("1,3", "1,3"))
        self.assertTrue(answer_matches(["2", "4"], ["2", "4"]))
        self.assertFalse(answer_matches("3", "1,3"))
        self.assertFalse(answer_matches("1,2,3", "1,3"))

    def test_single_answer_exact_match_stays_unchanged(self):
        self.assertTrue(answer_matches(" a ", "A"))
        self.assertFalse(answer_matches("B", "A"))

    def test_free_text_with_punctuation_is_not_split_into_partial_matches(self):
        self.assertFalse(answer_matches("서울", "서울, 부산"))


class MultipleCorrectAnswerGradingTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(
            name="Multiple Answer Tenant",
            code="multians",
            is_active=True,
        )
        self.admin = User.objects.create_user(
            username="multi_answer_admin",
            password="test1234",
            tenant=self.tenant,
            is_staff=True,
        )
        student_user = User.objects.create_user(
            username="multi_answer_student",
            password="test1234",
            tenant=self.tenant,
        )
        self.student = Student.objects.create(
            tenant=self.tenant,
            user=student_user,
            name="Multiple Answer Student",
        )
        self.lecture = Lecture.objects.create(
            tenant=self.tenant,
            title="Multiple Answer Lecture",
            name="Multiple Answer Lecture",
            subject="MATH",
        )
        self.enrollment = Enrollment.objects.create(
            tenant=self.tenant,
            student=self.student,
            lecture=self.lecture,
            status="ACTIVE",
        )
        self.exam = Exam.objects.create(
            tenant=self.tenant,
            title="Multiple Answer Exam",
            exam_type=Exam.ExamType.REGULAR,
            pass_score=0,
            max_score=15,
        )
        ExamEnrollment.objects.create(exam=self.exam, enrollment=self.enrollment)
        self.sheet = Sheet.objects.create(exam=self.exam, name="MAIN", total_questions=3)
        self.q1 = ExamQuestion.objects.create(sheet=self.sheet, number=1, score=5)
        self.q2 = ExamQuestion.objects.create(sheet=self.sheet, number=2, score=5)
        self.q3 = ExamQuestion.objects.create(sheet=self.sheet, number=3, score=5)
        AnswerKey.objects.create(
            exam=self.exam,
            answers={
                str(self.q1.id): "1,3",
                str(self.q2.id): "2|4",
                str(self.q3.id): "5",
            },
        )

    def _create_submission(self) -> Submission:
        submission = Submission.objects.create(
            tenant=self.tenant,
            user=self.admin,
            enrollment_id=self.enrollment.id,
            target_type=Submission.TargetType.EXAM,
            target_id=self.exam.id,
            source=Submission.Source.OMR_SCAN,
            status=Submission.Status.ANSWERS_READY,
        )
        SubmissionAnswer.objects.create(
            tenant=self.tenant,
            submission=submission,
            exam_question_id=self.q1.id,
            answer="1,3",
        )
        SubmissionAnswer.objects.create(
            tenant=self.tenant,
            submission=submission,
            exam_question_id=self.q2.id,
            answer="4",
        )
        SubmissionAnswer.objects.create(
            tenant=self.tenant,
            submission=submission,
            exam_question_id=self.q3.id,
            answer="2",
        )
        return submission

    @patch("apps.domains.results.services.grading_service.dispatch_progress_pipeline")
    def test_omr_grading_accepts_multiple_correct_answer_candidates(self, mock_dispatch):
        submission = self._create_submission()

        grade_submission(submission.id)

        exam_result = ExamResult.objects.get(submission=submission)
        self.assertEqual(float(exam_result.total_score), 10.0)
        self.assertTrue(exam_result.breakdown["1"]["correct"])
        self.assertTrue(exam_result.breakdown["2"]["correct"])
        self.assertFalse(exam_result.breakdown["3"]["correct"])

        result = Result.objects.get(
            target_type="exam",
            target_id=self.exam.id,
            enrollment_id=self.enrollment.id,
        )
        self.assertEqual(float(result.total_score), 10.0)

        items = {
            item.question_id: item
            for item in ResultItem.objects.filter(result=result)
        }
        self.assertTrue(items[self.q1.id].is_correct)
        self.assertTrue(items[self.q2.id].is_correct)
        self.assertFalse(items[self.q3.id].is_correct)
        mock_dispatch.assert_called_once_with(submission_id=submission.id)

    @patch("apps.domains.results.services.grading_service.dispatch_progress_pipeline")
    def test_decimal_auto_score_adjustment_is_added_to_objective_total(self, mock_dispatch):
        exam = Exam.objects.create(
            tenant=self.tenant,
            title="Decimal Auto Score Exam",
            exam_type=Exam.ExamType.REGULAR,
            pass_score=0,
            max_score=10,
        )
        ExamEnrollment.objects.create(exam=exam, enrollment=self.enrollment)
        sheet = Sheet.objects.create(
            exam=exam,
            name="DECIMAL",
            total_questions=3,
            choice_count=3,
            essay_count=0,
        )
        questions = [
            ExamQuestion.objects.create(sheet=sheet, number=number, score=3.3)
            for number in range(1, 4)
        ]
        AnswerKey.objects.create(
            exam=exam,
            answers={
                **{str(question.id): "1" for question in questions},
                SCORE_ADJUSTMENT_KEY: {"objective": 0.1},
            },
        )
        submission = Submission.objects.create(
            tenant=self.tenant,
            user=self.admin,
            enrollment_id=self.enrollment.id,
            target_type=Submission.TargetType.EXAM,
            target_id=exam.id,
            source=Submission.Source.OMR_SCAN,
            status=Submission.Status.ANSWERS_READY,
        )
        for question in questions:
            SubmissionAnswer.objects.create(
                tenant=self.tenant,
                submission=submission,
                exam_question_id=question.id,
                answer="1",
            )

        grade_submission(submission.id)

        score_shape = get_exam_score_shape(exam)
        self.assertEqual(round(float(score_shape.objective_max_score), 1), 10.0)
        self.assertEqual(round(float(score_shape.total_max_score), 1), 10.0)

        exam_result = ExamResult.objects.get(submission=submission)
        self.assertEqual(float(exam_result.total_score), 10.0)
        self.assertEqual(float(exam_result.objective_score), 10.0)
        self.assertEqual(float(exam_result.max_score), 10.0)

        result = Result.objects.get(
            target_type="exam",
            target_id=exam.id,
            enrollment_id=self.enrollment.id,
        )
        self.assertEqual(float(result.total_score), 10.0)
        self.assertEqual(float(result.objective_score), 10.0)
        self.assertEqual(float(result.max_score), 10.0)
        self.assertEqual(
            round(sum(float(item.max_score) for item in ResultItem.objects.filter(result=result)), 1),
            9.9,
        )
        mock_dispatch.assert_called_once_with(submission_id=submission.id)
