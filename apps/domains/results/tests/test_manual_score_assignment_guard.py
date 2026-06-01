from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.models import Tenant, TenantMembership
from apps.domains.enrollment.models import Enrollment, SessionEnrollment
from apps.domains.exams.models import AnswerKey, Exam, ExamEnrollment, ExamQuestion, Sheet
from apps.domains.lectures.models import Lecture, Session
from apps.domains.results.models import ExamAttempt, Result, ResultFact, ResultItem
from apps.domains.results.views.admin_exam_item_score_view import AdminExamItemScoreView
from apps.domains.results.views.admin_exam_objective_score_view import AdminExamObjectiveScoreView
from apps.domains.results.views.admin_exam_subjective_score_view import AdminExamSubjectiveScoreView
from apps.domains.results.views.admin_exam_total_score_view import AdminExamTotalScoreView
from apps.domains.students.models import Student


User = get_user_model()


class ManualExamScoreAssignmentGuardTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.tenant = Tenant.objects.create(name="Manual Guard", code="manual-guard", is_active=True)
        self.admin = User.objects.create_user(
            username="manual-guard-admin",
            password="pw1234",
            tenant=self.tenant,
            is_staff=True,
        )
        TenantMembership.ensure_active(tenant=self.tenant, user=self.admin, role="admin")

        self.lecture = Lecture.objects.create(
            tenant=self.tenant,
            title="Lecture",
            name="Lecture",
            subject="MATH",
        )
        self.session = Session.objects.create(lecture=self.lecture, order=1, title="S1")
        self.exam = Exam.objects.create(
            tenant=self.tenant,
            title="Exam",
            exam_type=Exam.ExamType.REGULAR,
            max_score=100,
            pass_score=60,
        )
        self.exam.sessions.add(self.session)

        self.assigned_enrollment = self._create_enrollment("assigned")
        self.unassigned_enrollment = self._create_enrollment("unassigned")
        self.session_roster_enrollment = self._create_enrollment("session-roster")
        ExamEnrollment.objects.create(exam=self.exam, enrollment=self.assigned_enrollment)
        SessionEnrollment.objects.create(
            tenant=self.tenant,
            session=self.session,
            enrollment=self.session_roster_enrollment,
        )

        self.sheet = Sheet.objects.create(exam=self.exam, name="MAIN", total_questions=1)
        self.question = ExamQuestion.objects.create(sheet=self.sheet, number=1, score=5)

    def _create_enrollment(self, suffix: str) -> Enrollment:
        user = User.objects.create_user(
            username=f"manual-guard-{suffix}",
            password="pw1234",
            tenant=self.tenant,
        )
        student = Student.objects.create(
            tenant=self.tenant,
            user=user,
            name=f"Student {suffix}",
            ps_number=f"MG-{suffix}",
            omr_code=f"MG{suffix.upper()}"[:8],
        )
        return Enrollment.objects.create(
            tenant=self.tenant,
            lecture=self.lecture,
            student=student,
            status="ACTIVE",
        )

    def _patch(self, view_cls, data=None, enrollment=None, **kwargs):
        request = self.factory.patch("/results/admin/exams/manual/", data or {"score": 10}, format="json")
        request.tenant = self.tenant
        force_authenticate(request, user=self.admin)
        return view_cls.as_view()(
            request,
            exam_id=self.exam.id,
            enrollment_id=(enrollment or self.unassigned_enrollment).id,
            **kwargs,
        )

    def _assert_no_manual_score_side_effects(self):
        self.assertFalse(
            ExamAttempt.objects.filter(
                exam=self.exam,
                enrollment=self.unassigned_enrollment,
            ).exists()
        )
        self.assertFalse(
            Result.objects.filter(
                target_type="exam",
                target_id=self.exam.id,
                enrollment=self.unassigned_enrollment,
            ).exists()
        )
        self.assertFalse(
            ResultFact.objects.filter(
                target_type="exam",
                target_id=self.exam.id,
                enrollment_id=self.unassigned_enrollment.id,
            ).exists()
        )
        self.assertFalse(
            ResultItem.objects.filter(
                result__target_type="exam",
                result__target_id=self.exam.id,
                result__enrollment=self.unassigned_enrollment,
            ).exists()
        )

    def test_total_score_rejects_unassigned_enrollment(self):
        response = self._patch(AdminExamTotalScoreView, {"score": 10, "max_score": 100})

        self.assertEqual(response.status_code, 400, response.data)
        self._assert_no_manual_score_side_effects()

    def test_total_score_accepts_linked_session_roster_and_materializes_exam_enrollment(self):
        response = self._patch(
            AdminExamTotalScoreView,
            {"score": 10, "max_score": 100},
            enrollment=self.session_roster_enrollment,
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(
            ExamEnrollment.objects.filter(
                exam=self.exam,
                enrollment=self.session_roster_enrollment,
            ).exists()
        )
        self.assertTrue(
            Result.objects.filter(
                target_type="exam",
                target_id=self.exam.id,
                enrollment=self.session_roster_enrollment,
                total_score=10,
            ).exists()
        )

    def test_objective_score_rejects_unassigned_enrollment(self):
        response = self._patch(AdminExamObjectiveScoreView, {"score": 10})

        self.assertEqual(response.status_code, 400, response.data)
        self._assert_no_manual_score_side_effects()

    def test_subjective_score_rejects_unassigned_enrollment(self):
        response = self._patch(AdminExamSubjectiveScoreView, {"score": 10})

        self.assertEqual(response.status_code, 400, response.data)
        self._assert_no_manual_score_side_effects()

    def test_item_score_rejects_unassigned_enrollment(self):
        response = self._patch(
            AdminExamItemScoreView,
            {"score": 3, "answer": "2"},
            question_id=self.question.id,
        )

        self.assertEqual(response.status_code, 400, response.data)
        self._assert_no_manual_score_side_effects()

    @patch("apps.domains.results.views.admin_exam_item_score_view.dispatch_progress_pipeline")
    def test_item_score_recomputes_required_multi_choice_answer_on_server(self, mock_dispatch):
        AnswerKey.objects.create(
            exam=self.exam,
            answers={str(self.question.id): "2,3"},
        )

        partial_response = self._patch(
            AdminExamItemScoreView,
            {"score": 5, "answer": "2"},
            enrollment=self.assigned_enrollment,
            question_id=self.question.id,
        )

        self.assertEqual(partial_response.status_code, 200, partial_response.data)
        item = ResultItem.objects.get(
            result__target_type="exam",
            result__target_id=self.exam.id,
            result__enrollment=self.assigned_enrollment,
            question_id=self.question.id,
        )
        self.assertEqual(float(item.score), 0.0)
        self.assertFalse(item.is_correct)

        full_response = self._patch(
            AdminExamItemScoreView,
            {"score": 0, "answer": "2,3"},
            enrollment=self.assigned_enrollment,
            question_id=self.question.id,
        )

        self.assertEqual(full_response.status_code, 200, full_response.data)
        item.refresh_from_db()
        self.assertEqual(float(item.score), 5.0)
        self.assertTrue(item.is_correct)
        mock_dispatch.assert_called()
