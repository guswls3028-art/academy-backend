import uuid
from datetime import timedelta
from unittest.mock import patch

from django.apps import apps
from django.test import TestCase
from django.urls import resolve
from django.utils import timezone
from rest_framework.test import force_authenticate

from apps.domains.results.tests import test_online_assessment_totals as fixtures
from apps.support.student_app.exam_dependencies import (
    create_online_exam_submission, dispatch_student_exam_submission,
)


class OnlineSubmissionIdempotencyTests(TestCase):
    setUp = fixtures.OnlineAssessmentTotalsTests.setUp

    def answers(self):
        return [{"exam_question_id": q.pk, "answer": str(q.number)} for q in self.questions]

    def submit(self, request_id=None, *, answers=None, legacy=False, expected_attempt_count=None, omit_enrollment=False):
        answers = self.answers() if answers is None else answers
        data = {"answers": answers}
        url = f"/api/v1/student/exams/{self.exam.pk}/submit/"
        if legacy:
            url = "/api/v1/submissions/submissions/"
            data = {"target_type": "exam", "target_id": self.exam.pk, "source": "online",
                    "enrollment_id": self.enrollment.pk, "payload": data}
            if omit_enrollment:
                data.pop("enrollment_id")
        if request_id is not None:
            data["client_request_id"] = str(request_id)
        if expected_attempt_count is not None:
            data["expected_attempt_count"] = expected_attempt_count
        request = self.factory.post(url, data, format="json")
        request.tenant = self.tenant
        force_authenticate(request, user=self.user)
        match = resolve(url)
        with patch("apps.domains.results.services.grading_service.dispatch_progress_pipeline"):
            return match.func(request, **match.kwargs)

    def attempts(self):
        return apps.get_model("results", "ExamAttempt").objects.filter(
            exam_id=self.exam.pk, enrollment=self.enrollment)

    def test_response_loss_retry_returns_original_without_consuming_retake(self):
        self.exam.allow_retake = True
        self.exam.max_attempts = 2
        self.exam.save(update_fields=["allow_retake", "max_attempts"])
        key = uuid.uuid4()
        first = self.submit(key)
        replay = self.submit(key)
        self.assertEqual(first.status_code, 201, first.data)
        self.assertEqual(replay.data, first.data)
        self.assertEqual(self.attempts().count(), 1)
        second = self.submit(uuid.uuid4())
        self.assertEqual(second.status_code, 201, second.data)
        self.assertEqual(self.attempts().count(), 2)
        old_replay = self.submit(key)
        self.assertEqual(old_replay.data["submission_id"], first.data["submission_id"])
        self.assertEqual(old_replay.data["status"], "superseded")
        self.assertEqual(self.attempts().get(is_representative=True).submission_id, second.data["submission_id"])

    def test_same_key_cannot_overwrite_answers(self):
        key = uuid.uuid4()
        first = self.submit(key)
        changed = self.answers()
        changed[0]["answer"] = "5"
        response = self.submit(key, answers=changed)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data["code"], "online_request_conflict")
        self.assertEqual(self.attempts().count(), 1)
        saved = apps.get_model("submissions", "Submission").objects.get(pk=first.data["submission_id"])
        self.assertEqual(saved.payload["answers"], self.answers())

    def test_delayed_duplicate_dispatch_cannot_supersede_a_later_retake(self):
        self.exam.allow_retake = True
        self.exam.max_attempts = 2
        self.exam.save(update_fields=["allow_retake", "max_attempts"])
        key = uuid.uuid4()
        stale = create_online_exam_submission(
            request_user=self.user, request_student=self.student, tenant=self.tenant,
            exam=self.exam, enrollment=self.enrollment, answers=self.answers(), client_request_id=key)
        self.assertEqual(self.submit(key).status_code, 201)
        second = self.submit(uuid.uuid4())
        self.assertEqual(second.status_code, 201, second.data)
        dispatch_student_exam_submission(stale)
        newest = apps.get_model("submissions", "Submission").objects.get(pk=second.data["submission_id"])
        self.assertEqual(newest.status, "done")
        stale.refresh_from_db()
        self.assertEqual(stale.status, "superseded")
        self.assertEqual(self.attempts().get(is_representative=True).submission_id, newest.pk)

    def test_second_tab_stale_attempt_count_cannot_consume_retake(self):
        self.exam.allow_retake = True
        self.exam.max_attempts = 2
        self.exam.save(update_fields=["allow_retake", "max_attempts"])
        self.assertEqual(self.submit(uuid.uuid4(), expected_attempt_count=0).status_code, 201)
        stale = self.submit(uuid.uuid4(), expected_attempt_count=0)
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(stale.data["code"], "online_attempt_changed")
        self.assertEqual(self.attempts().count(), 1)
        self.assertEqual(self.submit(uuid.uuid4(), expected_attempt_count=1).status_code, 201)

    def test_keyed_failure_retries_same_row(self):
        key = uuid.uuid4()
        with patch("apps.support.student_app.exam_dependencies.dispatch_student_exam_submission",
                   side_effect=RuntimeError("injected failure")):
            self.assertEqual(self.submit(key).status_code, 500)
        saved = apps.get_model("submissions", "Submission").objects.get(client_request_id=key)
        self.assertEqual(saved.status, "failed")
        # Admission was accepted on time; a processing outage must not turn the
        # deadline into loss of that accepted answer snapshot.
        type(saved).objects.filter(pk=saved.pk).update(created_at=timezone.now() - timedelta(minutes=1))
        self.exam.close_at = timezone.now() - timedelta(seconds=1)
        self.exam.save(update_fields=["close_at"])
        response = self.submit(key)
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["submission_id"], saved.pk)
        self.assertEqual(self.attempts().count(), 1)
        self.assertEqual(self.submit(uuid.uuid4()).status_code, 409)

    def test_retry_resumes_interrupted_admission_without_thirty_minute_wait(self):
        key = uuid.uuid4()
        saved = create_online_exam_submission(
            request_user=self.user, request_student=self.student, tenant=self.tenant,
            exam=self.exam, enrollment=self.enrollment, answers=self.answers(), client_request_id=key)
        response = self.submit(key)
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["submission_id"], saved.pk)
        self.assertEqual(response.data["status"], "done")

    def test_completed_retry_survives_close_and_missing_answer_key(self):
        key = uuid.uuid4()
        first = self.submit(key)
        self.exam.close_at = timezone.now() - timedelta(seconds=1)
        self.exam.save(update_fields=["close_at"])
        self.answer_key.delete()
        replay = self.submit(key)
        self.assertEqual(replay.status_code, 201, replay.data)
        self.assertEqual(replay.data, first.data)

    def test_invalid_key_rejected_without_rows(self):
        self.assertEqual(self.submit("not-a-uuid").status_code, 400)
        self.assertFalse(apps.get_model("submissions", "Submission").objects.exists())

    def test_legacy_missing_enrollment_is_validation_failure_without_rows(self):
        response = self.submit(uuid.uuid4(), legacy=True, omit_enrollment=True)
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn("enrollment_id", response.data)
        self.assertFalse(apps.get_model("submissions", "Submission").objects.exists())

    def test_legacy_dispatch_failure_uses_same_recovery_and_keeps_answers(self):
        key = uuid.uuid4()
        with patch("apps.support.student_app.exam_dependencies.dispatch_student_exam_submission",
                   side_effect=RuntimeError("injected failure")):
            response = self.submit(key, legacy=True)
        self.assertEqual(response.status_code, 500, response.data)
        saved = apps.get_model("submissions", "Submission").objects.get(client_request_id=key)
        self.assertEqual(saved.status, "failed")
        response = self.submit(key)
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["submission_id"], saved.pk)

    def test_legacy_missing_key_or_invalid_answers_do_not_create_submission(self):
        self.answer_key.delete()
        self.assertEqual(self.submit(legacy=True).status_code, 409)
        self.assertEqual(self.submit(legacy=True, answers=[]).status_code, 400)
        self.assertFalse(apps.get_model("submissions", "Submission").objects.exists())
