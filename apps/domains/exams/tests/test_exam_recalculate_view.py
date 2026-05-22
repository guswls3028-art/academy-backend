from __future__ import annotations

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.models import Tenant, TenantMembership
from apps.domains.enrollment.models import Enrollment, SessionEnrollment
from apps.domains.exams.models import AnswerKey, Exam, ExamEnrollment, ExamQuestion, Sheet
from apps.domains.exams.views.exam_recalculate_view import ExamRecalculateView
from apps.domains.lectures.models import Lecture, Session
from apps.domains.results.models import ExamResult, Result
from apps.domains.students.models import Student
from apps.domains.submissions.models import Submission, SubmissionAnswer


User = get_user_model()


class ExamRecalculateViewTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.tenant = Tenant.objects.create(name="Recalc Tenant", code="recalc", is_active=True)
        self.admin = User.objects.create_user(
            username="recalc_admin",
            password="test1234",
            tenant=self.tenant,
            is_staff=True,
        )
        TenantMembership.ensure_active(tenant=self.tenant, user=self.admin, role="admin")

        self.lecture = Lecture.objects.create(
            tenant=self.tenant,
            title="Recalc Lecture",
            name="Recalc Lecture",
            subject="MATH",
        )
        self.session = Session.objects.create(lecture=self.lecture, order=1, title="1회")

        student_user = User.objects.create_user(
            username="recalc_student",
            password="test1234",
            tenant=self.tenant,
        )
        self.student = Student.objects.create(
            tenant=self.tenant,
            user=student_user,
            name="Recalc Student",
            parent_phone="01000000000",
        )
        self.enrollment = Enrollment.objects.create(
            tenant=self.tenant,
            student=self.student,
            lecture=self.lecture,
            status="ACTIVE",
        )

        self.exam = Exam.objects.create(
            tenant=self.tenant,
            title="Recalc Exam",
            exam_type=Exam.ExamType.REGULAR,
            pass_score=0,
            max_score=10,
        )
        self.exam.sessions.add(self.session)
        SessionEnrollment.objects.create(
            tenant=self.tenant,
            session=self.session,
            enrollment=self.enrollment,
        )
        ExamEnrollment.objects.create(exam=self.exam, enrollment=self.enrollment)

        self.sheet = Sheet.objects.create(exam=self.exam, name="MAIN", total_questions=2)
        self.q1 = ExamQuestion.objects.create(sheet=self.sheet, number=1, score=5)
        self.q2 = ExamQuestion.objects.create(sheet=self.sheet, number=2, score=5)
        self.answer_key = AnswerKey.objects.create(
            exam=self.exam,
            answers={str(self.q1.id): "1", str(self.q2.id): "3"},
        )

    def _create_submission(self, *, status=Submission.Status.DONE):
        submission = Submission.objects.create(
            tenant=self.tenant,
            user=self.admin,
            enrollment_id=self.enrollment.id,
            target_type=Submission.TargetType.EXAM,
            target_id=self.exam.id,
            source=Submission.Source.OMR_SCAN,
            status=status,
        )
        SubmissionAnswer.objects.create(
            tenant=self.tenant,
            submission=submission,
            exam_question_id=self.q1.id,
            answer="1",
        )
        SubmissionAnswer.objects.create(
            tenant=self.tenant,
            submission=submission,
            exam_question_id=self.q2.id,
            answer="2",
        )
        return submission

    @patch("apps.domains.results.services.grading_service.dispatch_progress_pipeline")
    def test_recalculate_route_regrades_completed_submission(self, mock_dispatch):
        submission = self._create_submission()

        request = self.factory.post(f"/api/v1/exams/{self.exam.id}/recalculate/")
        request.tenant = self.tenant
        force_authenticate(request, user=self.admin)

        response = ExamRecalculateView.as_view()(request, exam_id=self.exam.id)

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["total"], 1)
        self.assertEqual(response.data["graded"], 1)
        self.assertEqual(response.data["skipped"], 0)
        self.assertEqual(response.data["failed"], [])

        submission.refresh_from_db()
        self.assertEqual(submission.status, Submission.Status.DONE)

        exam_result = ExamResult.objects.get(submission=submission)
        self.assertEqual(float(exam_result.total_score), 5.0)
        self.assertEqual(float(exam_result.max_score), 10.0)

        result = Result.objects.get(
            target_type="exam",
            target_id=self.exam.id,
            enrollment_id=self.enrollment.id,
        )
        self.assertEqual(float(result.total_score), 5.0)
        mock_dispatch.assert_called_once_with(submission_id=submission.id)

    @patch("apps.domains.results.services.grading_service.dispatch_progress_pipeline")
    def test_recalculate_skips_failed_submission_without_state_mutation(self, mock_dispatch):
        submission = self._create_submission(status=Submission.Status.FAILED)

        request = self.factory.post(f"/api/v1/exams/{self.exam.id}/recalculate/")
        request.tenant = self.tenant
        force_authenticate(request, user=self.admin)

        response = ExamRecalculateView.as_view()(request, exam_id=self.exam.id)

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["total"], 1)
        self.assertEqual(response.data["graded"], 0)
        self.assertEqual(response.data["skipped"], 1)
        self.assertEqual(response.data["failed"], [])

        submission.refresh_from_db()
        self.assertEqual(submission.status, Submission.Status.FAILED)
        self.assertFalse(ExamResult.objects.filter(submission=submission).exists())
        mock_dispatch.assert_not_called()
