"""Published ONLINE auto-grades must not stay pending solely due to legacy DRAFT."""
from unittest.mock import patch

from django.apps import apps
from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import resolve
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.models import Tenant, TenantMembership
from apps.domains.results.models import ExamResult, Result, ResultItem
from apps.domains.results.utils.exam_achievement import compute_exam_achievement
from apps.domains.results.views.session_scores_view import SessionScoresView


class OnlineAssessmentTotalsTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.tenant = Tenant.objects.create(code="online-total", name="Online totals", is_active=True)
        self.staff = get_user_model().objects.create_user(username="online-total-admin", tenant=self.tenant, is_staff=True)
        self.user = get_user_model().objects.create_user(username="online-total-child", tenant=self.tenant)
        TenantMembership.ensure_active(tenant=self.tenant, user=self.staff, role="admin")
        TenantMembership.ensure_active(tenant=self.tenant, user=self.user, role="student")
        self.student = apps.get_model("students", "Student").objects.create(
            tenant=self.tenant, user=self.user, name="Online child", ps_number="OT1", omr_code="OT000001")
        lecture = apps.get_model("lectures", "Lecture").objects.create(
            tenant=self.tenant, title="Online", name="Online", subject="MATH")
        self.session = apps.get_model("lectures", "Session").objects.create(lecture=lecture, order=1, title="First")
        self.enrollment = apps.get_model("enrollment", "Enrollment").objects.create(
            tenant=self.tenant, lecture=lecture, student=self.student, status="ACTIVE")
        apps.get_model("enrollment", "SessionEnrollment").objects.create(
            tenant=self.tenant, session=self.session, enrollment=self.enrollment)
        self.exam = apps.get_model("exams", "Exam").objects.create(
            tenant=self.tenant, title="Five objective questions", exam_type="regular", max_score=100, pass_score=50)
        self.exam.sessions.add(self.session)
        apps.get_model("exams", "ExamEnrollment").objects.create(exam=self.exam, enrollment=self.enrollment)
        self.sheet = apps.get_model("exams", "Sheet").objects.create(
            exam=self.exam, name="MAIN", total_questions=5, choice_count=5, essay_count=0)
        self.questions = [apps.get_model("exams", "ExamQuestion").objects.create(
            sheet=self.sheet, number=number, score=20) for number in range(1, 6)]
        self.answer_key = apps.get_model("exams", "AnswerKey").objects.create(
            exam=self.exam, answers={str(q.pk): str(q.number) for q in self.questions})

    def _submit_online(self, *, answers=None, expected_score=60, user=None, enrollment=None):
        url = f"/api/v1/student/exams/{self.exam.pk}/submit/"
        request = self.factory.post(url, {"answers": [
            {"exam_question_id": q.pk, "answer": answers[q.number - 1] if answers is not None
             else str(q.number) if q.number <= 3 else "1"}
            for q in self.questions
        ]}, format="json")
        request.tenant = self.tenant
        force_authenticate(request, user=user or self.user)
        match = resolve(url)
        # Async downstream progress is not the score/legacy-read policy under test.
        with patch("apps.domains.results.services.grading_service.dispatch_progress_pipeline"):
            response = match.func(request, **match.kwargs)
        self.assertEqual(response.status_code, 201, response.data)
        self.submission = apps.get_model("submissions", "Submission").objects.get(pk=response.data["submission_id"])
        self.result = Result.objects.get(target_type="exam", target_id=self.exam.pk, enrollment=enrollment or self.enrollment)
        self.legacy = ExamResult.objects.get(submission=self.submission)
        self.assertEqual(self.submission.source, "online")
        self.assertEqual(self.submission.status, "done")
        self.assertEqual(self.result.total_score, expected_score)
        self.assertEqual(self.legacy.status, "DRAFT")

    def _achievement(self):
        return compute_exam_achievement(enrollment_id=self.enrollment.pk, exam_id=self.exam.pk,
            session=self.session, total_score=self.result.total_score, pass_score=50,
            attempt_id=self.result.attempt_id, tenant=self.tenant)

    def _score_rows(self):
        request = self.factory.get("/")
        request.tenant = self.tenant
        force_authenticate(request, user=self.staff)
        response = SessionScoresView.as_view()(request, session_id=self.session.pk)
        self.assertEqual(response.status_code, 200, response.data)
        return response.data["rows"]

    def _score_row(self):
        return next(row for row in self._score_rows() if row["enrollment_id"] == self.enrollment.pk)

    def test_online_objective_legacy_draft_is_ready_without_rewriting_history(self):
        self._submit_online()
        before_legacy = ExamResult.objects.filter(pk=self.legacy.pk).values().get()
        before_result = Result.objects.filter(pk=self.result.pk).values().get()
        before_items = list(ResultItem.objects.filter(result=self.result).order_by("pk").values())
        for _ in range(2):
            self.assertFalse(self._achievement()["is_provisional"])
            row = self._score_row()
            self.assertEqual(row["exam_total"], {"score": 60.0, "max_score": 100.0,
                "selected_count": 1, "scored_count": 1, "complete": True})
        self.assertEqual(ExamResult.objects.filter(pk=self.legacy.pk).values().get(), before_legacy)
        self.assertEqual(Result.objects.filter(pk=self.result.pk).values().get(), before_result)
        self.assertEqual(list(ResultItem.objects.filter(result=self.result).order_by("pk").values()), before_items)

    def test_legacy_draft_omr_is_not_mistaken_for_ready_online(self):
        self._submit_online()
        self.submission.source = "omr_scan"
        self.submission.save(update_fields=["source"])
        self.assertTrue(self._achievement()["is_provisional"])
        self.assertFalse(self._score_row()["exam_total"]["complete"])
        self.legacy.refresh_from_db()
        self.assertEqual(self.legacy.status, "DRAFT")

    def test_online_teacher_owned_essay_remains_provisional(self):
        self.sheet.choice_count = 4
        self.sheet.essay_count = 1
        self.sheet.save(update_fields=["choice_count", "essay_count"])
        self.exam.grading_mode = "mixed"
        self.exam.choice_question_count = 4
        self.exam.save(update_fields=["grading_mode", "choice_question_count"])
        self.answer_key.answers[str(self.questions[-1].pk)] = "해설참조"
        self.answer_key.save(update_fields=["answers"])
        self._submit_online()
        self.assertTrue(self._achievement()["is_provisional"])
        total = self._score_row()["exam_total"]
        self.assertFalse(total["complete"])
        self.assertIsNone(total["score"])

    def test_incomplete_legacy_breakdown_remains_provisional(self):
        self._submit_online()
        self.legacy.breakdown.pop(next(iter(self.legacy.breakdown)))
        self.legacy.save(update_fields=["breakdown"])
        self.assertTrue(self._achievement()["is_provisional"])
        self.assertFalse(self._score_row()["exam_total"]["complete"])

    def test_failed_submission_or_attempt_cannot_publish_legacy_draft(self):
        self._submit_online()
        Attempt = apps.get_model("results", "ExamAttempt")
        attempt = Attempt.objects.get(pk=self.result.attempt_id)
        for target in (self.submission, attempt):
            with self.subTest(target=target._meta.model_name):
                target.status = "failed"
                target.save(update_fields=["status"])
                self.assertTrue(self._achievement()["is_provisional"])
                self.assertFalse(self._score_row()["exam_total"]["complete"])
                target.status = "done"
                target.save(update_fields=["status"])

    def test_missing_canonical_result_does_not_publish_legacy_draft(self):
        self._submit_online()
        Result.objects.filter(pk=self.result.pk).delete()
        self.assertTrue(self._achievement()["is_provisional"])
        self.assertFalse(self._score_row()["exam_total"]["complete"])
        self.legacy.refresh_from_db()
        self.assertEqual(self.legacy.status, "DRAFT")

    def test_other_tenant_staff_cannot_read_online_session_totals(self):
        self._submit_online()
        foreign = Tenant.objects.create(code="online-total-foreign", name="Foreign", is_active=True)
        staff = get_user_model().objects.create_user(username="online-total-foreign-admin", tenant=foreign, is_staff=True)
        TenantMembership.ensure_active(tenant=foreign, user=staff, role="admin")
        request = self.factory.get("/")
        request.tenant = foreign
        force_authenticate(request, user=staff)
        response = SessionScoresView.as_view()(request, session_id=self.session.pk)
        self.assertEqual(response.status_code, 404)

    def test_math_numeric_short_answer_is_automatic_and_ready(self):
        self.sheet.choice_count = 4
        self.sheet.essay_count = 1
        self.sheet.save(update_fields=["choice_count", "essay_count"])
        self.exam.grading_mode = "mixed"
        self.exam.choice_question_count = 4
        self.exam.save(update_fields=["grading_mode", "choice_question_count"])
        self.answer_key.answers[str(self.questions[-1].pk)] = "007"
        self.answer_key.save(update_fields=["answers"])
        self._submit_online(answers=["1", "2", "3", "1", "7"], expected_score=80)
        self.assertFalse(self._achievement()["is_provisional"])
        total = self._score_row()["exam_total"]
        self.assertTrue(total["complete"])
        self.assertEqual(total["score"], 80)

    def test_fully_graded_zero_is_complete_not_missing(self):
        self._submit_online(answers=["2", "1", "1", "1", "1"], expected_score=0)
        self.assertFalse(self._achievement()["is_provisional"])
        total = self._score_row()["exam_total"]
        self.assertTrue(total["complete"])
        self.assertEqual(total["score"], 0)
        self.assertEqual(total["scored_count"], 1)

    def test_later_online_attempt_does_not_replace_initial_total(self):
        self.exam.allow_retake = True
        self.exam.max_attempts = 2
        self.exam.save(update_fields=["allow_retake", "max_attempts"])
        self._submit_online()
        first_attempt_id = self.result.attempt_id
        first_legacy_id = self.legacy.pk
        self._submit_online(answers=["1", "2", "3", "4", "5"], expected_score=100)
        self.assertNotEqual(self.result.attempt_id, first_attempt_id)
        total = self._score_row()["exam_total"]
        self.assertTrue(total["complete"])
        self.assertEqual(total["score"], 60)
        self.assertEqual(ExamResult.objects.get(pk=first_legacy_id).status, "DRAFT")
        self.result.refresh_from_db()
        self.assertEqual(self.result.total_score, 100)

    def test_online_session_total_queries_do_not_grow_from_two_to_twenty_students(self):
        self._submit_online()

        def add_graded_student(index):
            user = get_user_model().objects.create_user(username=f"online-total-child-{index}", tenant=self.tenant)
            TenantMembership.ensure_active(tenant=self.tenant, user=user, role="student")
            student = apps.get_model("students", "Student").objects.create(
                tenant=self.tenant, user=user, name=f"Child {index}",
                ps_number=f"OT{index + 1}", omr_code=f"OT{index + 1:06d}")
            enrollment = apps.get_model("enrollment", "Enrollment").objects.create(
                tenant=self.tenant, lecture_id=self.session.lecture_id, student=student, status="ACTIVE")
            apps.get_model("enrollment", "SessionEnrollment").objects.create(
                tenant=self.tenant, session=self.session, enrollment=enrollment)
            apps.get_model("exams", "ExamEnrollment").objects.create(exam=self.exam, enrollment=enrollment)
            self._submit_online(user=user, enrollment=enrollment)

        add_graded_student(1)
        self._score_rows()  # Do not compare first-use framework initialization.
        with CaptureQueriesContext(connection) as small:
            first_rows = self._score_rows()
        self.assertEqual(len(first_rows), 2)
        for index in range(2, 20):
            add_graded_student(index)
        with CaptureQueriesContext(connection) as large:
            final_rows = self._score_rows()
        self.assertEqual(len(final_rows), 20)
        self.assertEqual(len(large), len(small))
        for row in [*first_rows, *final_rows]:
            self.assertEqual(row["exam_total"], {"score": 60.0, "max_score": 100.0,
                "selected_count": 1, "scored_count": 1, "complete": True})
