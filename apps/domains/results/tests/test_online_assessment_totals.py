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

    def _published_grades(self, *, parent=False):
        user = self.user
        if parent:
            user = get_user_model().objects.create_user(username="online-total-parent", tenant=self.tenant)
            TenantMembership.ensure_active(tenant=self.tenant, user=user, role="parent")
            parent_row = apps.get_model("parents", "Parent").objects.create(
                tenant=self.tenant, user=user, name="Parent", phone="01011112222")
            self.student.parent = parent_row
            self.student.save(update_fields=["parent"])
        request = self.factory.get("/api/v1/student/grades/", HTTP_X_STUDENT_ID=str(self.student.pk))
        request.tenant = self.tenant
        force_authenticate(request, user=user)
        match = resolve("/api/v1/student/grades/")
        response = match.func(request, **match.kwargs)
        self.assertEqual(response.status_code, 200, response.data)
        return response.data

    def test_published_incomplete_online_is_scoreless_for_student_and_parent_then_recovers(self):
        self._submit_online()
        self.exam.student_results_published = False
        self.exam.save(update_fields=["student_results_published"])
        self.assertEqual(self._published_grades()["exams"], [])
        self.exam.student_results_published = True
        self.exam.save(update_fields=["student_results_published"])
        complete_breakdown = self.legacy.breakdown.copy()
        self.legacy.breakdown.pop(next(iter(self.legacy.breakdown)))
        self.legacy.save(update_fields=["breakdown"])
        self.assertTrue(self._achievement()["is_provisional"])
        result_before = Result.objects.filter(pk=self.result.pk).values().get()
        legacy_before = ExamResult.objects.filter(pk=self.legacy.pk).values().get()
        staff_request = self.factory.get("/api/v1/results/admin/student-grades/", {"student_id": self.student.pk})
        staff_request.tenant = self.tenant
        force_authenticate(staff_request, user=self.staff)
        staff_match = resolve("/api/v1/results/admin/student-grades/")
        staff_response = staff_match.func(staff_request, **staff_match.kwargs)
        self.assertEqual(staff_response.status_code, 200, staff_response.data)
        self.assertEqual(staff_response.data["exams"][0]["total_score"], 60)
        self.assertEqual(staff_response.data["exam_summary"]["scored_count"], 1)
        self.assertEqual(staff_response.data["exam_summary"]["average_score_pct"], 60)
        self.assertEqual(len(staff_response.data["exam_trend"]), 1)
        for parent in (False, True):
            with self.subTest(parent=parent):
                payload = self._published_grades(parent=parent)
                exam = payload["exams"][0]
                self.assertTrue(exam["is_provisional"])
                self.assertIsNone(exam["total_score"])
                self.assertIsNone(exam["is_pass"])
                self.assertIsNone(exam["achievement"])
                self.assertIsNone(exam["rank"])
                self.assertEqual(exam["total_questions"], 0)
                self.assertEqual(exam["wrong_question_numbers"], [])
                self.assertEqual(payload["exam_summary"]["scored_count"], 0)
                self.assertEqual(payload["exam_trend"], [])
        self.assertEqual(Result.objects.filter(pk=self.result.pk).values().get(), result_before)
        self.assertEqual(ExamResult.objects.filter(pk=self.legacy.pk).values().get(), legacy_before)
        self.legacy.breakdown = complete_breakdown
        self.legacy.save(update_fields=["breakdown"])
        payload = self._published_grades()
        self.assertFalse(payload["exams"][0]["is_provisional"])
        self.assertEqual(payload["exams"][0]["total_score"], 60)
        self.assertEqual(payload["exam_summary"]["scored_count"], 1)
        self.result.refresh_from_db()
        self.legacy.refresh_from_db()
        self.assertEqual(self.result.total_score, 60)
        self.assertEqual(self.legacy.status, "DRAFT")

    def test_final_manual_grade_remains_published_without_online_snapshot_requirements(self):
        self._submit_online()
        self.exam.student_results_published = True
        self.exam.save(update_fields=["student_results_published"])
        self.submission.source = "omr_manual"
        self.submission.save(update_fields=["source"])
        self.legacy.status = "FINAL"
        self.legacy.breakdown = {}
        self.legacy.save(update_fields=["status", "breakdown"])
        exam = self._published_grades()["exams"][0]
        self.assertFalse(exam["is_provisional"])
        self.assertEqual(exam["total_score"], 60)
        self.assertTrue(exam["is_pass"])

    def test_completed_retake_does_not_publish_incomplete_first_attempt_score(self):
        self.exam.allow_retake = True
        self.exam.max_attempts = 2
        self.exam.student_results_published = True
        self.exam.save(update_fields=["allow_retake", "max_attempts", "student_results_published"])
        self._submit_online()
        first_legacy = self.legacy
        first_legacy.breakdown.pop(next(iter(first_legacy.breakdown)))
        first_legacy.save(update_fields=["breakdown"])
        self._submit_online(answers=["1", "2", "3", "4", "5"], expected_score=100)
        self.assertTrue(self._achievement()["is_provisional"])
        payload = self._published_grades()
        self.assertTrue(payload["exams"][0]["is_provisional"])
        self.assertIsNone(payload["exams"][0]["total_score"])
        self.assertEqual(payload["exam_summary"]["scored_count"], 0)
        self.assertFalse(self._score_row()["exam_total"]["complete"])

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
        self.exam.student_results_published = True
        self.exam.save(update_fields=["student_results_published"])
        projected = self._published_grades()["exams"][0]
        self.assertTrue(projected["is_provisional"])
        self.assertIsNone(projected["total_score"])

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
        self.exam.student_results_published = True
        self.exam.save(update_fields=["student_results_published"])
        payload = self._published_grades()
        self.assertFalse(payload["exams"][0]["is_provisional"])
        self.assertEqual(payload["exams"][0]["total_score"], 0)
        self.assertEqual(payload["exam_summary"]["scored_count"], 1)

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
        self.exam.student_results_published = True
        self.exam.save(update_fields=["student_results_published"])
        self.assertEqual(self._published_grades()["exams"][0]["total_score"], 60)

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

    def test_online_essay_teacher_zero_restores_published_totals_and_survives_regrade(self):
        self._assert_online_essay_published_roundtrip(teacher_score=0)

    def test_online_essay_teacher_partial_restores_published_totals_and_survives_regrade(self):
        self._assert_online_essay_published_roundtrip(teacher_score=15)

    def _assert_online_essay_published_roundtrip(self, *, teacher_score):
        self.sheet.choice_count = 4
        self.sheet.essay_count = 1
        self.sheet.save(update_fields=["choice_count", "essay_count"])
        self.exam.grading_mode = "mixed"
        self.exam.choice_question_count = 4
        self.exam.manual_grading_method = "score"
        self.exam.student_results_published = True
        self.exam.save(update_fields=["grading_mode", "choice_question_count", "manual_grading_method", "student_results_published"])
        essay = self.questions[-1]
        self.answer_key.answers.pop(str(essay.pk))
        self.answer_key.save(update_fields=["answers"])
        answer = "교사가 확인할 실제 온라인 서술형 답안"
        self._submit_online(answers=["1", "2", "3", "4", answer], expected_score=80)
        # Create the authorized selected-child parent once, then use the same
        # identity before grading, after grading and after automatic regrade.
        self._published_grades(parent=True)
        parent_user = self.student.parent.user

        def summary(user):
            path = "/api/v1/student/grades/"
            request = self.factory.get(path, HTTP_X_STUDENT_ID=str(self.student.pk))
            request.tenant = self.tenant
            force_authenticate(request, user=user)
            match = resolve(path)
            response = match.func(request, **match.kwargs)
            self.assertEqual(response.status_code, 200, response.data)
            return response.data

        for user in (self.user, parent_user):
            with self.subTest(phase="pending", role=user.pk):
                payload = summary(user)
                self.assertTrue(payload["exams"][0]["is_provisional"])
                self.assertIsNone(payload["exams"][0]["total_score"])
                self.assertEqual(payload["exam_summary"]["scored_count"], 0)
                self.assertFalse(payload["session_totals"][0]["exam_total"]["complete"])
        self.assertFalse(self._score_row()["exam_total"]["complete"])
        grid_path = f"/api/v1/results/admin/exams/{self.exam.pk}/manual-grading/"
        match = resolve(grid_path)
        request = self.factory.get(grid_path)
        request.tenant = self.tenant
        force_authenticate(request, user=self.staff)
        grid = match.func(request, **match.kwargs)
        self.assertEqual(grid.status_code, 200, grid.data)
        row = next(row for row in grid.data["rows"] if row["enrollment_id"] == self.enrollment.pk)
        self.assertEqual(row["cells"][str(essay.pk)]["answer"], answer)
        self.assertIsNone(row["cells"][str(essay.pk)]["score"])
        request = self.factory.post(grid_path, {"apply": True, "rows": [{
            "enrollment_id": self.enrollment.pk, "expected_version": row["expected_version"],
            "attendance": "present", "cells": {str(essay.pk): {"score": teacher_score}},
        }]}, format="json")
        request.tenant = self.tenant
        force_authenticate(request, user=self.staff)
        saved = match.func(request, **match.kwargs)
        self.assertEqual(saved.status_code, 200, saved.data)
        expected_total = {"score": float(80 + teacher_score), "max_score": 100.0,
                          "selected_count": 1, "scored_count": 1, "complete": True}
        for phase in ("teacher-saved", "automatic-regrade"):
            if phase == "automatic-regrade":
                from apps.domains.results.services.grading_service import grade_submission
                type(self.submission).objects.filter(pk=self.submission.pk).update(status="answers_ready")
                with patch("apps.domains.results.services.grading_service.dispatch_progress_pipeline"):
                    grade_submission(self.submission.pk, force_regrade=True)
            for user in (self.user, parent_user):
                with self.subTest(phase=phase, role=user.pk, teacher_score=teacher_score):
                    payload = summary(user)
                    self.assertFalse(payload["exams"][0]["is_provisional"])
                    self.assertEqual(payload["exams"][0]["total_score"], 80 + teacher_score)
                    self.assertEqual(payload["exam_summary"]["scored_count"], 1)
                    self.assertEqual(payload["exam_summary"]["average_score_pct"], 80 + teacher_score)
                    self.assertEqual(payload["session_totals"][0]["exam_total"], expected_total)
            self.assertEqual(self._score_row()["exam_total"], expected_total)
            item = ResultItem.objects.get(result=self.result, question_id=essay.pk)
            self.assertEqual(item.answer, answer)
            self.assertEqual(item.score, teacher_score)
