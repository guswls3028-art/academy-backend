from __future__ import annotations

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.models import Tenant, TenantMembership
from apps.domains.enrollment.models import Enrollment, SessionEnrollment
from apps.domains.exams.models import AnswerKey, Exam, ExamEnrollment, ExamQuestion, Sheet
from apps.domains.exams.views.answer_key_view import AnswerKeyViewSet
from apps.domains.exams.views.exam_view import ExamViewSet
from apps.domains.lectures.models import Lecture, Session
from apps.domains.results.models import ExamAttempt, Result, ResultFact, ResultItem
from apps.domains.results.views.admin_exam_manual_answers_view import AdminExamManualAnswersView
from apps.domains.students.models import Student
from apps.domains.submissions.models import Submission


User = get_user_model()


class ManualExamAnswersTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.tenant = Tenant.objects.create(name="Offline answers", code="offline-answers", is_active=True)
        self.admin = User.objects.create_user(
            username="offline-answers-admin", password="pw1234", tenant=self.tenant, is_staff=True,
        )
        TenantMembership.ensure_active(tenant=self.tenant, user=self.admin, role="admin")
        self.lecture = Lecture.objects.create(
            tenant=self.tenant, title="대수", name="대수", subject="MATH",
        )
        self.session = Session.objects.create(lecture=self.lecture, order=1, title="1차시")
        student_user = User.objects.create_user(
            username="offline-answers-student", password="pw1234", tenant=self.tenant,
        )
        student = Student.objects.create(
            tenant=self.tenant, user=student_user, name="학생", ps_number="OFF-001", omr_code="00000001",
        )
        self.enrollment = Enrollment.objects.create(
            tenant=self.tenant, lecture=self.lecture, student=student, status="ACTIVE",
        )
        SessionEnrollment.objects.create(
            tenant=self.tenant, session=self.session, enrollment=self.enrollment,
        )
        self.exam = Exam.objects.create(
            tenant=self.tenant, title="오프라인 시험", exam_type=Exam.ExamType.REGULAR,
            grading_mode=Exam.GradingMode.CHOICE, max_score=10, pass_score=6,
        )
        self.exam.sessions.add(self.session)
        ExamEnrollment.objects.create(exam=self.exam, enrollment=self.enrollment)
        sheet = Sheet.objects.create(
            exam=self.exam, name="MAIN", total_questions=2, choice_count=2, essay_count=0,
        )
        self.first = ExamQuestion.objects.create(sheet=sheet, number=1, score=5)
        self.second = ExamQuestion.objects.create(sheet=sheet, number=2, score=5)
        AnswerKey.objects.create(
            exam=self.exam, answers={str(self.first.id): "4", str(self.second.id): "2"},
        )

    def _post(self, *, answers=None, expected_version=None, apply=False, exam_id=None, enrollment_id=None, preview_token=None):
        if apply and preview_token is None:
            preview = self._post(
                answers=answers, expected_version=expected_version, apply=False,
                exam_id=exam_id, enrollment_id=enrollment_id,
            )
            if preview.status_code == 200:
                preview_token = preview.data["preview_token"]
        request = self.factory.post(
            "/manual-answers/",
            data={
                "answers": answers if answers is not None else {
                    str(self.first.id): "4", str(self.second.id): "3",
                },
                "expected_version": expected_version,
                "note": "오프라인 제출 답안 입력",
                "apply": apply,
                "preview_token": preview_token,
            },
            format="json",
        )
        request.tenant = self.tenant
        force_authenticate(request, user=self.admin)
        return AdminExamManualAnswersView.as_view()(
            request,
            exam_id=exam_id or self.exam.id,
            enrollment_id=enrollment_id or self.enrollment.id,
        )

    @patch("apps.domains.results.services.manual_exam_answers.dispatch_progress_pipeline")
    def test_preview_then_atomic_save_and_correct_without_submission(self, dispatch):
        preview = self._post()
        self.assertEqual(preview.status_code, 200, preview.data)
        self.assertFalse(preview.data["applied"])
        self.assertEqual(preview.data["total_score"], 5)
        self.assertFalse(Result.objects.filter(target_id=self.exam.id).exists())

        saved = self._post(apply=True)
        self.assertEqual(saved.status_code, 200, saved.data)
        self.assertTrue(saved.data["applied"])
        result = Result.objects.get(target_type="exam", target_id=self.exam.id)
        self.assertEqual(result.total_score, 5)
        self.assertEqual(result.max_score, 10)
        self.assertEqual(ResultItem.objects.filter(result=result, source="manual").count(), 2)
        self.assertEqual(ResultFact.objects.filter(target_id=self.exam.id, source="manual").count(), 2)
        self.assertEqual(ExamAttempt.objects.get(exam=self.exam).meta["source"], "manual_entry")
        self.assertFalse(Submission.objects.filter(target_id=self.exam.id).exists())
        dispatch.assert_called_once_with(exam_id=self.exam.id)

        read_request = self.factory.get("/manual-answers/")
        read_request.tenant = self.tenant
        force_authenticate(read_request, user=self.admin)
        draft = AdminExamManualAnswersView.as_view()(read_request, self.exam.id, self.enrollment.id)
        self.assertEqual(draft.status_code, 200)
        self.assertEqual(draft.data["expected_version"], saved.data["expected_version"])
        self.assertEqual(draft.data["answers"][str(self.second.id)], "3")

        corrected = self._post(
            answers={str(self.first.id): "4", str(self.second.id): "2"},
            expected_version=saved.data["expected_version"], apply=True,
        )
        self.assertEqual(corrected.status_code, 200, corrected.data)
        result.refresh_from_db()
        self.assertEqual(result.total_score, 10)
        self.assertEqual(ExamAttempt.objects.filter(exam=self.exam).count(), 1)
        self.assertEqual(ResultFact.objects.filter(target_id=self.exam.id, source="manual").count(), 4)

        stale = self._post(expected_version=saved.data["expected_version"], apply=True)
        self.assertEqual(stale.status_code, 400)
        result.refresh_from_db()
        self.assertEqual(result.total_score, 10)

    def test_existing_omr_must_use_review_flow(self):
        Submission.objects.create(
            tenant=self.tenant, user=self.admin, enrollment=self.enrollment,
            target_type=Submission.TargetType.EXAM, target_id=self.exam.id,
            source=Submission.Source.OMR_SCAN, status=Submission.Status.DONE,
        )
        response = self._post(apply=True)
        self.assertEqual(response.status_code, 400)
        self.assertFalse(Result.objects.filter(target_id=self.exam.id).exists())

    @patch("apps.domains.results.services.manual_exam_answers.dispatch_progress_pipeline")
    def test_late_offline_submission_replaces_confirmed_absence_with_audit(self, _dispatch):
        attempt = ExamAttempt.objects.create(
            exam=self.exam, enrollment=self.enrollment, submission_id=0,
            attempt_index=1, is_representative=True, status="done",
            meta={"source": "manual_grading_grid", "status": "NOT_SUBMITTED"},
        )
        result = Result.objects.create(
            target_type="exam", target_id=self.exam.id, enrollment=self.enrollment,
            attempt=attempt, total_score=0, max_score=10, objective_score=0,
        )
        response = self._post(expected_version=result.updated_at.isoformat(), apply=True)
        self.assertEqual(response.status_code, 200, response.data)
        result.refresh_from_db()
        attempt.refresh_from_db()
        self.assertEqual(result.total_score, 5)
        self.assertNotIn("status", attempt.meta)
        self.assertEqual(attempt.meta["source"], "manual_entry")
        self.assertEqual(attempt.meta["previous_absence"]["source"], "manual_grading_grid")

    def test_incomplete_or_foreign_question_set_is_rejected(self):
        response = self._post(answers={str(self.first.id): "4"}, apply=True)
        self.assertEqual(response.status_code, 400)
        response = self._post(answers={str(self.first.id): "4", str(self.second.id): "7"}, apply=True)
        self.assertEqual(response.status_code, 400)
        self.assertFalse(Result.objects.filter(target_id=self.exam.id).exists())

    def test_stale_preview_cannot_confirm_after_answer_key_change(self):
        preview = self._post()
        self.assertEqual(preview.status_code, 200)
        AnswerKey.objects.filter(exam=self.exam).update(answers={
            str(self.first.id): "4", str(self.second.id): "3",
        })
        confirmed = self._post(apply=True, preview_token=preview.data["preview_token"])
        self.assertEqual(confirmed.status_code, 400)
        self.assertFalse(Result.objects.filter(target_id=self.exam.id).exists())

    def test_non_target_and_other_tenant_are_rejected(self):
        other = Tenant.objects.create(name="Other", code="other-offline", is_active=True)
        other_exam = Exam.objects.create(
            tenant=other, title="Other", exam_type=Exam.ExamType.REGULAR,
        )
        response = self._post(exam_id=other_exam.id, apply=True)
        self.assertEqual(response.status_code, 404)
        other_student_user = User.objects.create_user(
            username="non-target", password="pw1234", tenant=self.tenant,
        )
        other_student = Student.objects.create(
            tenant=self.tenant, user=other_student_user, name="비대상", ps_number="OFF-002",
        )
        other_enrollment = Enrollment.objects.create(
            tenant=self.tenant, lecture=self.lecture, student=other_student, status="ACTIVE",
        )
        response = self._post(enrollment_id=other_enrollment.id, apply=True)
        self.assertEqual(response.status_code, 400)

    @patch("apps.domains.results.services.manual_exam_answers.dispatch_progress_pipeline")
    def test_projection_failure_rolls_back_all_manual_rows(self, dispatch):
        dispatch.side_effect = RuntimeError("projection failed")
        with self.assertRaises(RuntimeError):
            self._post(apply=True)
        self.assertFalse(Result.objects.filter(target_id=self.exam.id).exists())
        self.assertFalse(ResultFact.objects.filter(target_id=self.exam.id).exists())
        self.assertFalse(ExamAttempt.objects.filter(exam=self.exam).exists())

    @patch("apps.domains.results.services.manual_exam_answers.dispatch_progress_pipeline")
    def test_answer_key_correction_regrades_offline_entry_without_losing_answers(self, _dispatch):
        saved = self._post(apply=True)
        self.assertEqual(saved.status_code, 200, saved.data)
        key = AnswerKey.objects.get(exam=self.exam)
        request = self.factory.put(
            f"/api/v1/exams/answer-keys/{key.id}/",
            data={"exam": self.exam.id, "answers": {
                str(self.first.id): "4", str(self.second.id): "3",
            }},
            format="json",
        )
        request.tenant = self.tenant
        force_authenticate(request, user=self.admin)
        response = AnswerKeyViewSet.as_view({"put": "update"})(request, pk=key.id)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["regrade"][0]["manual_graded"], 1)
        result = Result.objects.get(target_type="exam", target_id=self.exam.id)
        self.assertEqual(result.total_score, 10)
        self.assertEqual(
            list(ResultItem.objects.filter(result=result).order_by("question_id").values_list("answer", flat=True)),
            ["4", "3"],
        )
        self.assertEqual(ResultFact.objects.filter(target_id=self.exam.id, source="manual").count(), 4)

    @patch("apps.domains.exams.views.exam_view.dispatch_progress_for_exam")
    @patch("apps.domains.results.services.manual_exam_answers.dispatch_progress_pipeline")
    def test_max_score_correction_refreshes_manual_denominator(self, _manual_dispatch, _exam_dispatch):
        saved = self._post(apply=True)
        self.assertEqual(saved.status_code, 200, saved.data)
        request = self.factory.patch(
            f"/api/v1/exams/{self.exam.id}/", data={"max_score": 12}, format="json",
        )
        request.tenant = self.tenant
        force_authenticate(request, user=self.admin)
        response = ExamViewSet.as_view({"patch": "partial_update"})(request, pk=self.exam.id)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["regrade"]["manual_graded"], 1)
        result = Result.objects.get(target_type="exam", target_id=self.exam.id)
        self.assertEqual(result.total_score, 5)
        self.assertEqual(result.max_score, 12)
        _exam_dispatch.assert_called_once_with(exam_id=self.exam.id)

    @patch("apps.domains.exams.views.exam_view.dispatch_progress_for_exam")
    def test_cutoff_projection_failure_rolls_back_setting(self, dispatch):
        dispatch.side_effect = RuntimeError("projection failed")
        request = self.factory.patch(
            f"/api/v1/exams/{self.exam.id}/", data={"pass_score": 7}, format="json",
        )
        request.tenant = self.tenant
        force_authenticate(request, user=self.admin)
        with self.assertRaises(RuntimeError):
            ExamViewSet.as_view({"patch": "partial_update"})(request, pk=self.exam.id)
        self.exam.refresh_from_db()
        self.assertEqual(self.exam.pass_score, 6)

    @patch("apps.domains.results.services.manual_exam_answers.dispatch_progress_pipeline")
    def test_answer_key_change_reports_later_teacher_score_override(self, _dispatch):
        saved = self._post(apply=True)
        self.assertEqual(saved.status_code, 200)
        result = Result.objects.get(target_type="exam", target_id=self.exam.id)
        result.total_score = 6
        result.save(update_fields=["total_score", "updated_at"])
        key = AnswerKey.objects.get(exam=self.exam)
        request = self.factory.put(
            f"/api/v1/exams/answer-keys/{key.id}/",
            data={"exam": self.exam.id, "answers": {
                str(self.first.id): "4", str(self.second.id): "3",
            }}, format="json",
        )
        request.tenant = self.tenant
        force_authenticate(request, user=self.admin)
        response = AnswerKeyViewSet.as_view({"put": "update"})(request, pk=key.id)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(len(response.data["regrade"][0]["needs_review"]), 1, response.data)
        result.refresh_from_db()
        self.assertEqual(result.total_score, 6)

    @patch("apps.domains.exams.views.answer_key_view.regrade_exam_submissions")
    def test_answer_key_failure_rolls_back_changed_key(self, regrade):
        regrade.return_value = {"failed": [{"submission_id": 1, "detail": "failed"}]}
        key = AnswerKey.objects.get(exam=self.exam)
        request = self.factory.put(
            f"/api/v1/exams/answer-keys/{key.id}/",
            data={"exam": self.exam.id, "answers": {
                str(self.first.id): "4", str(self.second.id): "3",
            }}, format="json",
        )
        request.tenant = self.tenant
        force_authenticate(request, user=self.admin)
        response = AnswerKeyViewSet.as_view({"put": "update"})(request, pk=key.id)
        self.assertEqual(response.status_code, 400)
        key.refresh_from_db()
        self.assertEqual(key.answers[str(self.second.id)], "2")
