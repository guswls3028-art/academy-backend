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

    def submit(self, request_id=None, *, answers=None, legacy=False, expected_attempt_count=None,
               omit_enrollment=False, **extra):
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
        data.update(extra)
        request = self.factory.post(url, data, format="json")
        request.tenant = self.tenant
        force_authenticate(request, user=self.user)
        match = resolve(url)
        with patch("apps.domains.results.services.grading_service.dispatch_progress_pipeline"):
            return match.func(request, **match.kwargs)

    def attempts(self):
        return apps.get_model("results", "ExamAttempt").objects.filter(
            exam_id=self.exam.pk, enrollment=self.enrollment)

    def add_other_course(self, title="Shared"):
        lecture = apps.get_model("lectures", "Lecture").objects.create(
            tenant=self.tenant, title=title, name=title, subject="MATH")
        session = type(self.session).objects.create(lecture=lecture, order=1, title="Second class")
        self.exam.sessions.add(session)
        enrollment = type(self.enrollment).objects.create(tenant=self.tenant, lecture=lecture, student=self.student)
        apps.get_model("enrollment", "SessionEnrollment").objects.create(
            tenant=self.tenant, session=session, enrollment=enrollment)
        apps.get_model("exams", "ExamEnrollment").objects.create(exam=self.exam, enrollment=enrollment)
        return session

    def inbox(self, sid, *, choices=True):
        path = "/api/v1/submissions/submissions/pending/"
        request = self.factory.get(path, {"filter": "done", **({"session_choices": "1"} if choices else {})})
        request.tenant = self.tenant
        force_authenticate(request, user=self.staff)
        response = resolve(path).func(request)
        self.assertEqual(response.status_code, 200)
        return next(row for row in response.data if row["id"] == sid)

    def test_verified_entry_and_grade_keep_independent_identity_across_retries_and_retakes(self):
        other = self.add_other_course()
        self.exam.allow_retake, self.exam.max_attempts = True, 2
        self.exam.save(update_fields=["allow_retake", "max_attempts"])
        key = uuid.uuid4()
        first = self.submit(key, entry_session_id=other.pk, meta={"student_entry_session_id": self.session.pk})
        self.assertEqual(first.status_code, 201, first.data)
        self.assertEqual(self.submit(key, entry_session_id=other.pk).data, first.data)
        self.assertEqual(self.submit(key).data, first.data)
        self.assertEqual(self.submit(key, entry_session_id=self.session.pk).status_code, 409)
        sid = first.data["submission_id"]
        row = self.inbox(sid)
        self.assertEqual((row["session_id"], row["lecture_id"], row["score_lecture_id"], row["enrollment_id"]),
                         (other.id, other.lecture_id, self.enrollment.lecture_id, self.enrollment.pk))
        self.assertIs(row["session_origin_recorded"], True)
        self.assertEqual(self.inbox(sid, choices=False)["session_id"], self.session.pk)
        self.assertEqual(self.attempts().count(), 1)
        retake = self.submit(uuid.uuid4(), entry_session_id=self.session.pk)
        self.assertEqual(retake.status_code, 201, retake.data)
        self.assertEqual(self.inbox(retake.data["submission_id"])["session_id"], self.session.pk)
        first_submission = apps.get_model("submissions", "Submission").objects.get(pk=sid)
        self.assertEqual(first_submission.meta["student_entry_session_id"], other.pk)
        self.assertEqual(self.attempts().count(), 2)
        self.enrollment.status = "INACTIVE"
        self.enrollment.save(update_fields=["status"])
        self.assertEqual(self.submit(key, entry_session_id=other.pk).status_code, 409)
        self.assertEqual(self.attempts().count(), 2)

    def test_unrecorded_entry_shows_possible_courses_without_backfilling(self):
        other = self.add_other_course()
        key = uuid.uuid4()
        first = self.submit(key, meta={"student_entry_session_id": other.pk})
        self.assertEqual(first.status_code, 201, first.data)
        sid = first.data["submission_id"]
        row = self.inbox(sid)
        self.assertIs(row["session_origin_recorded"], False)
        self.assertIsNone(row["session_id"])
        self.assertEqual([s["session_id"] for s in row["session_choices"]], [self.session.id, other.id])
        self.assertEqual(row["score_lecture_id"], self.enrollment.lecture_id)
        self.assertEqual(self.submit(key, entry_session_id=other.pk).status_code, 409)
        self.add_other_course("Later enrollment")
        self.assertEqual(self.inbox(sid), row)
        legacy = self.inbox(sid, choices=False)
        self.assertEqual(legacy["session_id"], self.session.id)
        self.assertNotIn("session_origin_recorded", legacy)
        self.assertNotIn("score_lecture_id", legacy)

    def test_unrecorded_submission_offers_only_current_sessions_after_partial_reenrollment(self):
        other = self.add_other_course()
        first = self.submit(uuid.uuid4())
        self.assertEqual(first.status_code, 201, first.data)
        sid = first.data["submission_id"]
        attendance = apps.get_model("attendance", "Attendance")
        other_enrollment = type(self.enrollment).objects.get(student=self.student, lecture_id=other.lecture_id)
        attendance.objects.create(tenant=self.tenant, session=other, enrollment=other_enrollment, status="SECESSION")
        self.assertEqual(self.inbox(sid)["session_id"], self.session.id)

        attendance.objects.create(tenant=self.tenant, session=self.session, enrollment=self.enrollment, status="SECESSION")
        history = self.inbox(sid)
        self.assertTrue(history["target_resolved"])
        self.assertTrue(history["session_history_only"])
        self.assertIsNone(history["session_id"])
        self.assertEqual(history["session_choices"], [])
        self.assertEqual(history["enrollment_id"], self.enrollment.pk)
        self.assertEqual(self.inbox(sid, choices=False)["session_id"], self.session.pk)

        returning = type(self.session).objects.create(lecture_id=self.session.lecture_id, order=2, title="Restored")
        self.exam.sessions.add(returning)
        apps.get_model("enrollment", "SessionEnrollment").objects.create(
            tenant=self.tenant, session=returning, enrollment=self.enrollment)
        row = self.inbox(sid)
        self.assertEqual(row["session_id"], returning.id)
        self.assertFalse(row["session_origin_recorded"])
        self.assertNotIn("session_history_only", row)
        self.assertEqual(row["score_lecture_id"], self.enrollment.lecture_id)

    def test_invalid_and_unauthorized_sessions_cannot_create_a_grade(self):
        other = self.add_other_course()
        Attendance = apps.get_model("attendance", "Attendance")
        enrollment = type(self.enrollment).objects.get(student=self.student, lecture_id=other.lecture_id)
        Attendance.objects.create(tenant=self.tenant, session=other, enrollment=enrollment, status="ABSENT")
        for sid, status in [(True, 400), ("abc", 400), (2**63, 400), (other.id, 403), (other.id+100, 403)]:
            with self.subTest(session_id=sid):
                response = self.submit(uuid.uuid4(), entry_session_id=sid)
                self.assertEqual(response.status_code, status, response.data)
        self.assertFalse(apps.get_model("submissions", "Submission").objects.exists())
        self.assertEqual(self.attempts().count(), 0)

    def test_recorded_source_survives_a_withdrawn_score_session_and_opens_history(self):
        first = self.submit(uuid.uuid4(), entry_session_id=self.session.pk)
        self.assertEqual(first.status_code, 201, first.data)
        sid = first.data["submission_id"]
        self.assertNotIn("session_history_only", self.inbox(sid))
        apps.get_model("attendance", "Attendance").objects.create(
            tenant=self.tenant, session=self.session, enrollment=self.enrollment, status="SECESSION")
        row = self.inbox(sid)
        self.assertEqual((row["target_resolved"], row["session_history_only"], row["session_origin_recorded"]),
                         (True, True, True))
        self.assertEqual((row["session_id"], row["session_title"], row["score_lecture_id"]),
                         (self.session.pk, self.session.title, self.enrollment.lecture_id))
        path = "/api/v1/results/admin/landing-stats/"
        for params in ({}, {"session_choices": "1"}):
            request = self.factory.get(path, params)
            request.tenant = self.tenant
            force_authenticate(request, user=self.staff)
            summary = resolve(path).func(request)
            recent = next(item for item in summary.data["recent_done_top"] if item["id"] == sid)
            if params:
                self.assertEqual((recent["session_history_only"], recent["session_title"]), (True, self.session.title))
            else:
                self.assertNotIn("session_title", recent)
                self.assertNotIn("session_history_only", recent)

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

    def test_staff_submission_history_keeps_first_score_after_a_retake(self):
        self.exam.allow_retake, self.exam.max_attempts = True, 2
        self.exam.save(update_fields=["allow_retake", "max_attempts"])
        wrong = [{"exam_question_id": q.pk, "answer": "9"} for q in self.questions]
        first = self.submit(uuid.uuid4(), answers=wrong)
        second = self.submit(uuid.uuid4())
        self.assertEqual((first.status_code, second.status_code), (201, 201))
        path = f"/api/v1/submissions/submissions/exams/{self.exam.pk}/"
        for response, score in ((first, 0), (second, 100)):
            sid = response.data["submission_id"]
            request = self.factory.get(path, {"focus_id": sid})
            request.tenant = self.tenant
            force_authenticate(request, user=self.staff)
            result = resolve(path).func(request, exam_id=self.exam.pk)
            self.assertEqual(result.status_code, 200)
            self.assertEqual([(r["id"], r["enrollment_id"], r["score"]) for r in result.data],
                             [(sid, self.enrollment.pk, score)])

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
        original_key = self.answer_key.answers
        self.answer_key.answers = {}
        self.answer_key.save(update_fields=["answers"])
        rejected = self.submit(key)
        self.assertEqual(rejected.status_code, 409, rejected.data)
        saved.refresh_from_db()
        self.assertEqual(saved.status, "failed")
        self.assertEqual(self.attempts().count(), 0)
        self.answer_key.answers = original_key
        self.answer_key.save(update_fields=["answers"])
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
