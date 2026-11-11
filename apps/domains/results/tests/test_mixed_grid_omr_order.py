"""Mixed exams: essay grading must not depend on the order scans are matched.

Reproduces the field report: entering essay scores in the grading grid failed
with "선택형 OMR 채점을 먼저 완료해 주세요", marking the student absent as a
workaround then made the later OMR match fail with a retake conflict.
"""
from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient, APIRequestFactory, force_authenticate

from apps.core.models import Tenant, TenantMembership
from apps.domains.results.models import ExamAttempt, ExamResult, Result, ResultItem
from apps.domains.results.services.grading_service import grade_submission
from apps.domains.results.services.manual_exam_grading import (
    apply_manual_grading,
    build_manual_grading_sheet,
    plan_manual_grading,
)
from apps.domains.results.services.omr_subjective_completion import (
    omr_subjective_completion_states,
    pending_omr_result_ids,
)
from apps.domains.results.views.session_scores_view import SessionScoresView
from apps.support.results.tests.omr_subjective_completion_fixtures import (
    AnswerKey,
    Enrollment,
    Exam,
    ExamEnrollment,
    ExamQuestion,
    Lecture,
    Session,
    SessionEnrollment,
    Sheet,
    Student,
    Submission,
    SubmissionAnswer,
)

User = get_user_model()


class MixedGridOmrOrderTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(code="mixed-grid-order", name="Mixed grid order", is_active=True)
        self.staff = User.objects.create_user(
            username="mixed-grid-staff", password="pw1234", tenant=self.tenant, is_staff=True,
        )
        TenantMembership.ensure_active(tenant=self.tenant, user=self.staff, role="admin")
        student_user = User.objects.create_user(
            username="mixed-grid-student", password="pw1234", tenant=self.tenant,
        )
        self.student = Student.objects.create(
            tenant=self.tenant, user=student_user, name="Grid student",
            ps_number="MIXED-GRID-1", omr_code="12345678",
        )
        self.lecture = Lecture.objects.create(
            tenant=self.tenant, title="Grid lecture", name="Grid lecture", subject="MATH",
        )
        self.session = Session.objects.create(lecture=self.lecture, order=1, title="1주차")
        self.enrollment = Enrollment.objects.create(
            tenant=self.tenant, student=self.student, lecture=self.lecture, status="ACTIVE",
        )
        SessionEnrollment.objects.create(tenant=self.tenant, session=self.session, enrollment=self.enrollment)
        self.exam = Exam.objects.create(
            tenant=self.tenant, title="주간 테스트", exam_type=Exam.ExamType.REGULAR,
            grading_mode=Exam.GradingMode.MIXED, manual_grading_method="score",
            pass_score=60, max_score=100, student_results_published=True,
        )
        self.exam.sessions.add(self.session)
        ExamEnrollment.objects.create(exam=self.exam, enrollment=self.enrollment)
        sheet = Sheet.objects.create(
            exam=self.exam, name="MAIN", total_questions=2, choice_count=1, essay_count=1,
        )
        self.choice = ExamQuestion.objects.create(
            sheet=sheet, number=1, score=80, question_kind=ExamQuestion.QuestionKind.CHOICE,
        )
        self.essay = ExamQuestion.objects.create(
            sheet=sheet, number=2, score=20, question_kind=ExamQuestion.QuestionKind.ESSAY,
        )
        AnswerKey.objects.create(
            exam=self.exam, answers={str(self.choice.id): "1", str(self.essay.id): "해설참조"},
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.staff)

    # -- helpers -----------------------------------------------------------

    def _scan(self, *, matched: bool = True) -> Submission:
        submission = Submission.objects.create(
            tenant=self.tenant, user=self.staff,
            enrollment=self.enrollment if matched else None,
            target_type=Submission.TargetType.EXAM, target_id=self.exam.id,
            source=Submission.Source.OMR_SCAN,
            status=(
                Submission.Status.ANSWERS_READY if matched
                else Submission.Status.NEEDS_IDENTIFICATION
            ),
            meta={"manual_review": {"required": False}},
        )
        SubmissionAnswer.objects.create(
            tenant=self.tenant, submission=submission,
            exam_question_id=self.choice.id, answer="1",
        )
        return submission

    def _sheet_row(self):
        sheet = build_manual_grading_sheet(exam=self.exam, tenant=self.tenant)
        return next(row for row in sheet["rows"] if row["enrollment_id"] == self.enrollment.id)

    def _grid(self, *, attendance="present", essay=None):
        row = self._sheet_row()
        cells = {} if essay is None else {str(self.essay.id): {"score": essay}}
        plan = plan_manual_grading(
            exam=self.exam, tenant=self.tenant,
            payload={"rows": [{
                "enrollment_id": self.enrollment.id,
                "expected_version": row["expected_version"],
                "attendance": attendance,
                "cells": cells,
            }]},
        )
        self.assertTrue(plan.can_apply, plan.errors)
        payload = apply_manual_grading(plan=plan, user_id=self.staff.id)
        return plan, payload

    def _result(self) -> Result:
        return Result.objects.get(target_type="exam", target_id=self.exam.id, enrollment=self.enrollment)

    def _manual_edit(self, submission, **extra):
        return self.client.post(
            f"/api/v1/submissions/submissions/{submission.id}/manual-edit/",
            {"identifier": {"enrollment_id": self.enrollment.id}, **extra},
            format="json", HTTP_HOST="api.hakwonplus.com",
            HTTP_X_TENANT_CODE=self.tenant.code,
        )

    def _session_block(self):
        request = APIRequestFactory().get(f"/results/admin/sessions/{self.session.id}/scores/")
        request.tenant = self.tenant
        force_authenticate(request, user=self.staff)
        response = SessionScoresView.as_view()(request, session_id=self.session.id)
        self.assertEqual(response.status_code, 200, getattr(response, "data", None))
        row = next(row for row in response.data["rows"] if row["enrollment_id"] == self.enrollment.id)
        return next(entry["block"] for entry in row["exams"] if entry["exam_id"] == self.exam.id)

    # -- scenarios -----------------------------------------------------------

    def test_essay_scores_saved_before_scan_are_held_then_combined(self):
        self.assertEqual(self._sheet_row()["objective_status"], "omr_pending")
        plan, payload = self._grid(essay=15)
        self.assertEqual(plan.rows[0].objective_status, "omr_pending")
        self.assertEqual(payload["objective_pending_count"], 1)

        result = self._result()
        self.assertEqual(float(result.total_score), 15)
        self.assertEqual(float(result.objective_score), 0)
        state = omr_subjective_completion_states([result])[result.id]
        self.assertTrue(state.pending)
        self.assertEqual(state.pending_reason, "objective_pending")
        self.assertIn(result.id, pending_omr_result_ids([result]))
        block = self._session_block()
        self.assertEqual(block["grading_status"], "subjective_pending")
        self.assertEqual(block["grading_pending_reason"], "objective_pending")
        self.assertFalse(block["clinic_required"])

        scan = self._scan()
        grade_submission(scan.id)

        result.refresh_from_db()
        self.assertEqual(float(result.objective_score), 80)
        self.assertEqual(float(result.total_score), 95)
        attempts = list(ExamAttempt.objects.filter(exam=self.exam, enrollment=self.enrollment))
        self.assertEqual(len(attempts), 1)
        attempt = attempts[0]
        self.assertEqual(attempt.submission_id, scan.id)
        self.assertNotIn("awaiting_omr_objective", attempt.meta)
        self.assertEqual(attempt.meta["initial_snapshot"]["source"], "omr_attached_manual_essay_items")
        self.assertEqual(attempt.meta["initial_snapshot"]["total_score"], 95)
        self.assertIn("awaiting_omr_objective", attempt.meta["manual_score_placeholder"])
        self.assertEqual(
            ExamResult.objects.get(submission=scan, exam=self.exam).status, ExamResult.Status.FINAL,
        )
        self.assertEqual(pending_omr_result_ids([result]), set())
        self.assertEqual(
            float(ResultItem.objects.get(result=result, question=self.essay).score), 15,
        )

    def test_returning_scanned_student_from_absence_restores_objective(self):
        scan = self._scan()
        grade_submission(scan.id)
        self._grid(attendance="absent")
        result = self._result()
        self.assertEqual(float(result.total_score), 0)
        self.assertFalse(ResultItem.objects.filter(result=result).exists())

        self.assertEqual(self._sheet_row()["objective_status"], None)
        plan, payload = self._grid(essay=12)
        self.assertEqual(plan.rows[0].objective_status, "omr_restore")
        self.assertEqual(payload["objective_restore_count"], 1)

        result.refresh_from_db()
        self.assertEqual(float(result.objective_score), 80)
        self.assertEqual(float(result.total_score), 92)
        attempt = ExamAttempt.objects.get(exam=self.exam, enrollment=self.enrollment)
        self.assertEqual(attempt.submission_id, scan.id)
        self.assertNotIn("status", attempt.meta)
        self.assertEqual(attempt.meta["absence_resumptions"][-1]["via"], "manual_grid")
        self.assertEqual(
            ExamResult.objects.get(submission=scan, exam=self.exam).status, ExamResult.Status.FINAL,
        )
        self.assertEqual(pending_omr_result_ids([result]), set())

    def test_match_after_grid_absence_offers_explicit_resume(self):
        scan = self._scan(matched=False)
        self._grid(attendance="absent")
        placeholder = ExamAttempt.objects.get(exam=self.exam, enrollment=self.enrollment)
        self.assertEqual(placeholder.meta["status"], "NOT_SUBMITTED")

        response = self._manual_edit(scan)
        self.assertEqual(response.status_code, 409, response.data)
        self.assertEqual(response.data["code"], "OMR_ATTEMPT_CONFLICT")
        self.assertTrue(response.data["can_resume_absence"])
        scan.refresh_from_db()
        placeholder.refresh_from_db()
        self.assertIsNone(scan.enrollment_id)
        self.assertEqual(placeholder.meta["status"], "NOT_SUBMITTED")
        self.assertEqual(placeholder.submission_id, 0)

        response = self._manual_edit(scan, resume_absence=True)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(response.data["graded"])
        self.assertTrue(response.data["absence_resumed"])
        self.assertFalse(response.data["absence_preserved"])
        self.assertEqual(response.data["grading_status"], "subjective_pending")
        placeholder.refresh_from_db()
        self.assertEqual(placeholder.submission_id, scan.id)
        self.assertNotIn("status", placeholder.meta)
        self.assertNotIn("absence_resume", placeholder.meta)
        self.assertEqual(
            placeholder.meta["absence_placeholder"]["explicit_resume"]["confirmed_submission_id"],
            scan.id,
        )
        self.assertEqual(ExamAttempt.objects.filter(exam=self.exam, enrollment=self.enrollment).count(), 1)
        result = self._result()
        self.assertEqual(float(result.objective_score), 80)

        # The essay can now be entered normally and the result finalizes.
        self.assertIsNone(self._sheet_row()["objective_status"])
        self._grid(essay=20)
        result.refresh_from_db()
        self.assertEqual(float(result.total_score), 100)
        self.assertEqual(pending_omr_result_ids([result]), set())

    def test_reconfirming_scan_of_absent_student_reports_then_resumes(self):
        scan = self._scan()
        grade_submission(scan.id)
        self._grid(attendance="absent")

        response = self._manual_edit(scan)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(response.data["absence_preserved"])
        self.assertTrue(response.data["can_resume_absence"])
        result = self._result()
        self.assertEqual(float(result.total_score), 0)

        response = self._manual_edit(scan, resume_absence=True)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(response.data["absence_resumed"])
        self.assertFalse(response.data["absence_preserved"])
        result.refresh_from_db()
        self.assertEqual(float(result.objective_score), 80)
        attempt = ExamAttempt.objects.get(exam=self.exam, enrollment=self.enrollment)
        self.assertNotIn("status", attempt.meta)
        self.assertEqual(attempt.meta["absence_resumptions"][-1]["via"], "omr_review")

    def test_essays_saved_while_scan_awaits_review_complete_on_confirmation(self):
        scan = self._scan()
        scan.meta = {"manual_review": {"required": True, "reasons": ["MULTI_MARK"]}}
        scan.save(update_fields=["meta", "updated_at"])
        grade_submission(scan.id)
        self.assertFalse(
            Result.objects.filter(target_type="exam", target_id=self.exam.id).exists()
        )

        self.assertEqual(self._sheet_row()["objective_status"], "omr_pending")
        self._grid(essay=18)
        result = self._result()
        self.assertEqual(
            omr_subjective_completion_states([result])[result.id].pending_reason,
            "objective_pending",
        )

        response = self._manual_edit(scan)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(response.data["graded"])
        self.assertTrue(response.data["projection_ready"], response.data)
        result.refresh_from_db()
        self.assertEqual(float(result.objective_score), 80)
        self.assertEqual(float(result.total_score), 98)
        attempt = ExamAttempt.objects.get(exam=self.exam, enrollment=self.enrollment)
        self.assertEqual(attempt.submission_id, scan.id)
        self.assertEqual(pending_omr_result_ids([result]), set())
