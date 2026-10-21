"""Online submit failures, retakes and teacher-owned essays across real API routes."""
from datetime import timedelta
from unittest.mock import patch

from django.apps import apps
from django.test import TestCase
from django.urls import resolve
from django.utils import timezone
from rest_framework.test import force_authenticate

from apps.domains.results.models import Result, ResultItem
from apps.domains.results.tests import test_online_assessment_totals as fixtures


class OnlineSubmissionRecoveryTests(TestCase):
    setUp = fixtures.OnlineAssessmentTotalsTests.setUp
    _submit_online = fixtures.OnlineAssessmentTotalsTests._submit_online

    def call(self, url, *, data=None, user=None):
        request = (self.factory.get(url) if data is None
                   else self.factory.post(url, data, format="json"))
        request.tenant = self.tenant
        force_authenticate(request, user=user or self.user)
        match = resolve(url)
        with patch("apps.domains.results.services.grading_service.dispatch_progress_pipeline"):
            return match.func(request, **match.kwargs)

    def submit(self):
        return self.call(f"/api/v1/student/exams/{self.exam.pk}/submit/", data={"answers": [
            {"exam_question_id": q.pk, "answer": str(q.number)} for q in self.questions
        ]})

    def submission(self, **overrides):
        return apps.get_model("submissions", "Submission").objects.create(**{
            "tenant": self.tenant, "user": self.user, "enrollment": self.enrollment,
            "target_type": "exam", "target_id": self.exam.pk, "source": "online",
            "status": "failed", "payload": {"answers": []}, **overrides,
        })

    def test_missing_answer_key_rejects_before_persistence_then_recovers(self):
        answers = self.answer_key.answers.copy()
        self.answer_key.delete()
        response = self.submit()
        self.assertEqual(response.status_code, 409, response.data)
        self.assertIn("정답 준비", response.data["detail"])
        self.assertFalse(apps.get_model("submissions", "Submission").objects.exists())
        self.assertFalse(Result.objects.exists())
        apps.get_model("exams", "AnswerKey").objects.create(exam=self.exam, answers=answers)
        self.assertEqual(self.submit().status_code, 201)

    def test_empty_partial_and_blank_keys_never_store_zero_grade(self):
        for answers in ({}, {str(self.questions[0].pk): "1"},
                        {str(q.pk): " " for q in self.questions}):
            with self.subTest(answers=answers):
                self.answer_key.answers = answers
                self.answer_key.save(update_fields=["answers"])
                self.assertEqual(self.submit().status_code, 409)
                self.assertFalse(Result.objects.exists())
                self.assertFalse(apps.get_model("submissions", "Submission").objects.exists())

    def test_dispatch_failure_preserves_answer_and_allows_retry(self):
        with patch("apps.domains.student_app.exams.views.dispatch_student_exam_submission",
                   side_effect=RuntimeError("injected grading failure")):
            response = self.submit()
        self.assertEqual(response.status_code, 500)
        failed = apps.get_model("submissions", "Submission").objects.get()
        self.assertEqual(failed.status, "failed")
        self.assertEqual(len(failed.payload["answers"]), 5)
        self.assertFalse(Result.objects.exists())
        retry = self.submit()
        self.assertEqual(retry.status_code, 201, retry.data)
        self.assertEqual(retry.data["status"], "done")
        self.assertEqual(Result.objects.get().total_score, 100)

    def test_only_stale_ungraded_online_submission_can_be_recovered(self):
        pending = self.submission(status="submitted")
        self.assertEqual(self.submit().status_code, 409)
        type(pending).objects.filter(pk=pending.pk).update(updated_at=timezone.now() - timedelta(hours=1))
        response = self.submit()
        self.assertEqual(response.status_code, 201, response.data)
        pending.refresh_from_db()
        self.assertEqual(pending.status, "failed")
        self.assertEqual(pending.payload, {"answers": []})

    def test_failed_records_do_not_consume_retakes_and_limit_still_applies(self):
        self.exam.allow_retake = True
        self.exam.max_attempts = 2
        self.exam.save(update_fields=["allow_retake", "max_attempts"])
        self.submission()
        self._submit_online()
        detail = self.call(f"/api/v1/student/exams/{self.exam.pk}/")
        result = self.call(f"/api/v1/student/results/me/exams/{self.exam.pk}/")
        self.assertEqual(detail.data["attempt_count"], 1)
        self.assertTrue(result.data["can_retake"])
        with patch("apps.domains.submissions.services.dispatcher.dispatch_submission",
                   side_effect=RuntimeError("injected retake failure")):
            self.assertEqual(self.submit().status_code, 500)
        self.submission.refresh_from_db()
        self.assertEqual(self.submission.status, "done")
        self.assertEqual(self.call(f"/api/v1/student/results/me/exams/{self.exam.pk}/").data["total_score"], 60)
        self.assertEqual(self.submit().status_code, 201)
        self.assertEqual(self.call(f"/api/v1/student/exams/{self.exam.pk}/").data["attempt_count"], 2)
        self.assertEqual(self.submit().status_code, 409)

    def test_essay_answer_survives_teacher_grading_reload_and_regrade(self):
        self.assert_essay_roundtrip(score=15)

    def test_teacher_zero_is_a_completed_grade_not_pending(self):
        self.assert_essay_roundtrip(score=0)

    def test_numeric_zero_remains_automatically_graded(self):
        self.sheet.choice_count = 4
        self.sheet.essay_count = 1
        self.sheet.save(update_fields=["choice_count", "essay_count"])
        self.answer_key.answers[str(self.questions[-1].pk)] = 0
        self.answer_key.save(update_fields=["answers"])
        self._submit_online(answers=["1", "2", "3", "4", "000"], expected_score=100)
        published = self.call(f"/api/v1/student/results/me/exams/{self.exam.pk}/")
        self.assertEqual(published.data["total_score"], 100)

    def assert_essay_roundtrip(self, *, score):
        self.sheet.choice_count = 4
        self.sheet.essay_count = 1
        self.sheet.save(update_fields=["choice_count", "essay_count"])
        self.exam.grading_mode = "mixed"
        self.exam.choice_question_count = 4
        self.exam.manual_grading_method = "score"
        self.exam.save(update_fields=["grading_mode", "choice_question_count", "manual_grading_method"])
        essay = self.questions[-1]
        self.answer_key.answers.pop(str(essay.pk))
        self.answer_key.save(update_fields=["answers"])
        answer = "계산 과정과 근거를 설명합니다.\n두 번째 줄에도 답안이 이어집니다."
        self._submit_online(answers=["1", "2", "3", "4", answer], expected_score=80)
        result_url = f"/api/v1/student/results/me/exams/{self.exam.pk}/"
        pending = self.call(result_url)
        self.assertEqual(pending.status_code, 200, pending.data)
        self.assertEqual(pending.data["grading_status"], "subjective_pending")
        self.assertNotIn("total_score", pending.data)
        detail_url = f"/api/v1/results/admin/exams/{self.exam.pk}/enrollments/{self.enrollment.pk}/"
        detail = self.call(detail_url, user=self.staff)
        self.assertEqual(detail.status_code, 200, detail.data)
        item = next(i for i in detail.data["items"] if i["question_id"] == essay.pk)
        self.assertEqual(item["answer"], answer)
        self.assertIsNone(item["score"])
        self.assertIsNone(item["is_correct"])
        grid_url = f"/api/v1/results/admin/exams/{self.exam.pk}/manual-grading/"
        sheet = self.call(grid_url, user=self.staff)
        row = next(r for r in sheet.data["rows"] if r["enrollment_id"] == self.enrollment.pk)
        self.assertEqual(row["cells"][str(essay.pk)]["answer"], answer)
        self.assertIsNone(row["cells"][str(essay.pk)]["score"])
        saved = self.call(grid_url, user=self.staff, data={"apply": True, "rows": [{
            "enrollment_id": self.enrollment.pk, "expected_version": row["expected_version"],
            "attendance": "present", "cells": {str(essay.pk): {"score": score}},
        }]})
        self.assertEqual(saved.status_code, 200, saved.data)
        for _ in range(2):
            published = self.call(result_url)
            self.assertEqual(published.status_code, 200, published.data)
            self.assertEqual(published.data["total_score"], 80 + score)
            item = ResultItem.objects.get(result=self.result, question_id=essay.pk)
            self.assertEqual(item.answer, answer)
            self.assertEqual(item.score, score)
        from apps.domains.results.services.grading_service import grade_submission
        type(self.submission).objects.filter(pk=self.submission.pk).update(status="answers_ready")
        with patch("apps.domains.results.services.grading_service.dispatch_progress_pipeline"):
            grade_submission(self.submission.id, force_regrade=True)
        self.assertEqual(self.call(result_url).data["total_score"], 80 + score)
        self.assertEqual(ResultItem.objects.get(result=self.result, question_id=essay.pk).answer, answer)
