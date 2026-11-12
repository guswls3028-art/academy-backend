"""Beta OMR teacher score strip: exam setting and grading-grid proposals.

The strip is read by the AI worker into ``meta.ai_result.result.essay_scores``.
The grading grid only proposes those scores; saving stays the teacher's action.
"""
from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from apps.core.models import Tenant, TenantMembership
from apps.domains.results.models import Result, ResultItem
from apps.domains.results.services.grading_service import grade_submission
from apps.domains.results.services.manual_exam_grading import (
    apply_manual_grading,
    build_manual_grading_sheet,
    plan_manual_grading,
)
from apps.support.omr.contract_builder import build_omr_sheet_contract
from apps.support.omr.payload_builder import build_omr_payload
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


class OmrEssayScoreProposalTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(code="essay-strip", name="Essay strip", is_active=True)
        self.staff = User.objects.create_user(
            username="essay-strip-staff", password="pw1234", tenant=self.tenant, is_staff=True,
        )
        TenantMembership.ensure_active(tenant=self.tenant, user=self.staff, role="admin")
        self.lecture = Lecture.objects.create(
            tenant=self.tenant, title="Strip lecture", name="Strip lecture", subject="MATH",
        )
        self.session = Session.objects.create(lecture=self.lecture, order=1, title="1주차")
        self.exam = Exam.objects.create(
            tenant=self.tenant, title="주간 테스트", exam_type=Exam.ExamType.REGULAR,
            grading_mode=Exam.GradingMode.MIXED, manual_grading_method="score",
            pass_score=60, max_score=100, student_results_published=True,
        )
        self.exam.sessions.add(self.session)
        self.sheet = Sheet.objects.create(
            exam=self.exam, name="MAIN", total_questions=2, choice_count=1, essay_count=1,
        )
        self.choice = ExamQuestion.objects.create(
            sheet=self.sheet, number=1, score=80, question_kind=ExamQuestion.QuestionKind.CHOICE,
        )
        self.essay = ExamQuestion.objects.create(
            sheet=self.sheet, number=2, score=20, question_kind=ExamQuestion.QuestionKind.ESSAY,
        )
        AnswerKey.objects.create(
            exam=self.exam, answers={str(self.choice.id): "1", str(self.essay.id): "해설참조"},
        )
        self.enrollment = self._student("A", "11112222")
        self.client = APIClient()
        self.client.force_authenticate(user=self.staff)

    # -- helpers -----------------------------------------------------------

    def _student(self, suffix: str, omr_code: str) -> Enrollment:
        user = User.objects.create_user(
            username=f"essay-strip-student-{suffix}", password="pw1234", tenant=self.tenant,
        )
        student = Student.objects.create(
            tenant=self.tenant, user=user, name=f"Strip {suffix}",
            ps_number=f"STRIP-{suffix}", omr_code=omr_code,
        )
        enrollment = Enrollment.objects.create(
            tenant=self.tenant, student=student, lecture=self.lecture, status="ACTIVE",
        )
        SessionEnrollment.objects.create(tenant=self.tenant, session=self.session, enrollment=enrollment)
        ExamEnrollment.objects.create(exam=self.exam, enrollment=enrollment)
        return enrollment

    def _api(self, method: str, path: str, data=None):
        return getattr(self.client, method)(
            f"/api/v1/exams/{self.exam.id}/omr/{path}",
            data, format="json", HTTP_HOST="api.hakwonplus.com",
            HTTP_X_TENANT_CODE=self.tenant.code,
        )

    def _graded_scan(self, enrollment, essay_scores) -> Submission:
        submission = Submission.objects.create(
            tenant=self.tenant, user=self.staff, enrollment=enrollment,
            target_type=Submission.TargetType.EXAM, target_id=self.exam.id,
            source=Submission.Source.OMR_SCAN, status=Submission.Status.ANSWERS_READY,
            meta={
                "manual_review": {"required": False},
                "ai_result": {"status": "DONE", "result": {"essay_scores": essay_scores}},
            },
        )
        SubmissionAnswer.objects.create(
            tenant=self.tenant, submission=submission,
            exam_question_id=self.choice.id, answer="1",
        )
        grade_submission(submission.id)
        return submission

    def _row(self, enrollment, sheet=None):
        sheet = sheet or build_manual_grading_sheet(exam=self.exam, tenant=self.tenant)
        return next(row for row in sheet["rows"] if row["enrollment_id"] == enrollment.id)

    # -- setting -------------------------------------------------------------

    def test_setting_toggles_printed_strip_and_worker_payload(self):
        response = self._api("get", "essay-score-marks/")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(
            {key: response.data[key] for key in ("enabled", "available", "printed", "beta")},
            {"enabled": False, "available": True, "printed": False, "beta": True},
        )
        before = build_omr_sheet_contract(sheet=self.sheet).fingerprint()

        response = self._api("patch", "essay-score-marks/", {"enabled": True})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertTrue(response.data["enabled"])
        self.assertTrue(response.data["printed"])
        self.sheet.refresh_from_db()
        self.assertTrue(self.sheet.essay_score_marks)

        contract = build_omr_sheet_contract(sheet=self.sheet)
        self.assertNotEqual(contract.fingerprint(), before)
        self.assertEqual([mark["question_number"] for mark in contract.essay_score_marks], [2])
        defaults = self._api("get", "defaults/")
        self.assertEqual(defaults.status_code, 200, defaults.content)
        self.assertTrue(defaults.data["essay_score_marks"])
        # The print screen echoes the exam shape: the strip is printed. A changed
        # shape would not match the scanning contract, so it prints without strips.
        same = self._api("post", "preview/", {"mc_count": 1, "essay_count": 1})
        self.assertEqual(same.status_code, 200)
        self.assertIn('class="es-a"', same.content.decode("utf-8"))
        changed = self._api("post", "preview/", {"mc_count": 1, "essay_count": 2})
        self.assertEqual(changed.status_code, 200)
        self.assertNotIn('class="es-a"', changed.content.decode("utf-8"))
        pdf = self._api("post", "pdf/", {"mc_count": 1, "essay_count": 1})
        self.assertEqual(pdf.status_code, 200)
        self.assertTrue(pdf.content.startswith(b"%PDF"))

        scan = Submission.objects.create(
            tenant=self.tenant, user=self.staff, target_type=Submission.TargetType.EXAM,
            target_id=self.exam.id, source=Submission.Source.OMR_SCAN,
            payload={"sheet_id": self.sheet.id},
        )
        payload = build_omr_payload(scan)
        self.assertEqual(
            [mark["question_number"] for mark in payload["template_meta"]["essay_score_marks"]], [2],
        )

        response = self._api("patch", "essay-score-marks/", {"enabled": False})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertFalse(response.data["printed"])
        self.sheet.refresh_from_db()
        self.assertEqual(build_omr_sheet_contract(sheet=self.sheet).fingerprint(), before)

    def test_enabling_an_unfit_sheet_is_rejected_with_reason(self):
        self.essay.score = 60
        self.essay.save(update_fields=["score"])
        response = self._api("patch", "essay-score-marks/", {"enabled": True})
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn("49.9점", str(response.data["enabled"]))
        self.sheet.refresh_from_db()
        self.assertFalse(self.sheet.essay_score_marks)
        missing = self._api("patch", "essay-score-marks/", {})
        self.assertEqual(missing.status_code, 400)

    def test_other_tenant_cannot_read_or_change_the_setting(self):
        other = Tenant.objects.create(code="essay-strip-other", name="Other", is_active=True)
        other_staff = User.objects.create_user(
            username="essay-strip-other-staff", password="pw1234", tenant=other, is_staff=True,
        )
        TenantMembership.ensure_active(tenant=other, user=other_staff, role="admin")
        client = APIClient()
        client.force_authenticate(user=other_staff)
        for method, data in (("get", None), ("patch", {"enabled": True})):
            response = getattr(client, method)(
                f"/api/v1/exams/{self.exam.id}/omr/essay-score-marks/", data, format="json",
                HTTP_HOST="api.hakwonplus.com", HTTP_X_TENANT_CODE=other.code,
            )
            self.assertEqual(response.status_code, 404, (method, response.content))
        self.sheet.refresh_from_db()
        self.assertFalse(self.sheet.essay_score_marks)

    # -- grading grid proposals ---------------------------------------------

    def test_grid_proposes_read_scores_and_teacher_save_finalizes(self):
        self.sheet.essay_score_marks = True
        self.sheet.save(update_fields=["essay_score_marks"])
        other = self._student("B", "33334444")
        third = self._student("C", "55556666")
        absent = self._student("D", "77778888")
        scan = self._graded_scan(self.enrollment, [{"question_number": 2, "status": "ok", "score": 12.3}])
        self._graded_scan(other, [{"question_number": 2, "status": "ok", "score": 25.0}])
        self._graded_scan(third, [{"question_number": 2, "status": "ambiguous", "score": None}])

        sheet = build_manual_grading_sheet(exam=self.exam, tenant=self.tenant)
        essay_key = str(self.essay.id)
        self.assertEqual(
            self._row(self.enrollment, sheet)["omr_essay_scores"],
            {essay_key: {"status": "ok", "score": 12.3, "submission_id": scan.id}},
        )
        self.assertEqual(self._row(other, sheet)["omr_essay_scores"][essay_key]["status"], "out_of_range")
        self.assertEqual(self._row(third, sheet)["omr_essay_scores"][essay_key]["status"], "needs_check")
        self.assertEqual(self._row(absent, sheet)["omr_essay_scores"], {})
        self.assertEqual(
            sheet["omr_essay_score_marks"],
            {"enabled": True, "beta": True, "proposal_count": 1, "needs_check_count": 2},
        )
        # A proposal is not a score: the essay cell stays ungraded until saved.
        self.assertIsNone(self._row(self.enrollment, sheet)["cells"][essay_key]["score"])
        self.assertFalse(
            ResultItem.objects.filter(result__enrollment=self.enrollment, question=self.essay).exists()
        )

        row = self._row(self.enrollment, sheet)
        plan = plan_manual_grading(
            exam=self.exam, tenant=self.tenant,
            payload={"rows": [{
                "enrollment_id": self.enrollment.id,
                "expected_version": row["expected_version"],
                "attendance": "present",
                "cells": {essay_key: {"score": 12.3}},
            }]},
        )
        self.assertTrue(plan.can_apply, plan.errors)
        apply_manual_grading(plan=plan, user_id=self.staff.id)
        result = Result.objects.get(target_type="exam", target_id=self.exam.id, enrollment=self.enrollment)
        self.assertAlmostEqual(float(result.total_score), 92.3)

    def test_blank_or_unprinted_strip_and_absent_rows_propose_nothing(self):
        self._graded_scan(self.enrollment, [{"question_number": 2, "status": "blank", "score": None}])
        other = self._student("B", "33334444")
        self._graded_scan(other, [{"question_number": 2, "status": "no_score_area", "score": None}])
        sheet = build_manual_grading_sheet(exam=self.exam, tenant=self.tenant)
        self.assertEqual(self._row(self.enrollment, sheet)["omr_essay_scores"], {})
        self.assertEqual(self._row(other, sheet)["omr_essay_scores"], {})
        self.assertFalse(sheet["omr_essay_score_marks"]["enabled"])

        absent = self._student("C", "55556666")
        self._graded_scan(absent, [{"question_number": 2, "status": "ok", "score": 7.0}])
        self.assertEqual(self._row(absent)["omr_essay_scores"][str(self.essay.id)]["score"], 7.0)
        row = self._row(absent)
        plan = plan_manual_grading(
            exam=self.exam, tenant=self.tenant,
            payload={"rows": [{
                "enrollment_id": absent.id,
                "expected_version": row["expected_version"],
                "attendance": "absent",
                "cells": {},
            }]},
        )
        self.assertTrue(plan.can_apply, plan.errors)
        apply_manual_grading(plan=plan, user_id=self.staff.id)
        self.assertEqual(self._row(absent)["omr_essay_scores"], {})
