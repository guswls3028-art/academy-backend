"""Paper grades and current homework targets must reach the progress projection."""

from unittest.mock import patch

from django.apps import apps
from django.contrib.auth import get_user_model
from django.db import transaction
from django.test import TestCase
from rest_framework.test import APIClient

from apps.core.models import Tenant, TenantMembership
from apps.domains.homework_results.models import Homework, HomeworkScore
from apps.domains.homework_results.services.policy_recalc import recalc_scores_for_homework_change
from apps.domains.progress.models import LectureProgress, ProgressPolicy, SessionProgress
from apps.domains.progress.services.progress_pipeline import ProgressPipelineService


class HomeworkScoreProgressTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Score progress", code="score-progress")
        self.teacher = get_user_model().objects.create_user(
            username="score-progress-teacher", tenant=self.tenant, is_staff=True,
        )
        TenantMembership.ensure_active(tenant=self.tenant, user=self.teacher, role="teacher")
        self.lecture = apps.get_model("lectures", "Lecture").objects.create(
            tenant=self.tenant, title="Lecture", name="Lecture",
        )
        self.session = apps.get_model("lectures", "Session").objects.create(
            lecture=self.lecture, title="Session", order=1,
        )
        student = apps.get_model("students", "Student").objects.create(
            tenant=self.tenant, name="Student", ps_number="SCORE-1", omr_code="12345678",
            user=get_user_model().objects.create_user(username="score-progress-student", tenant=self.tenant),
        )
        self.enrollment = apps.get_model("enrollment", "Enrollment").objects.create(
            tenant=self.tenant, lecture=self.lecture, student=student, status="ACTIVE",
        )
        apps.get_model("enrollment", "SessionEnrollment").objects.create(
            tenant=self.tenant, session=self.session, enrollment=self.enrollment,
        )
        self.homework = self._homework("First")
        self.policy = ProgressPolicy.objects.create(
            lecture=self.lecture, homework_start_session_order=1,
            homework_pass_type=ProgressPolicy.HomeworkPassType.SCORE,
        )
        SessionProgress.objects.create(
            enrollment=self.enrollment, session=self.session, attendance_type="offline",
            video_progress_rate=93, homework_submitted=False,
        )
        apps.get_model("results", "ScoreEditDraft").objects.create(
            tenant=self.tenant, session=self.session, editor_user=self.teacher,
            payload={"client_id": "score-progress-tab", "changes": []},
        )
        self.client = APIClient()
        self.client.force_authenticate(self.teacher)
        self.headers = {
            "HTTP_HOST": "localhost", "HTTP_X_TENANT_CODE": self.tenant.code,
            "HTTP_X_SCORE_EDITOR_CLIENT": "score-progress-tab",
            "HTTP_X_SCORE_SESSION_ID": str(self.session.id),
        }
        notice = patch("apps.domains.progress.services.clinic_resolution_service._send_resolution_notification")
        notice.start()
        self.addCleanup(notice.stop)
        self._recompute()

    def _homework(self, title, *, assigned=True, removed=False):
        homework = Homework.objects.create(
            tenant=self.tenant, session=self.session, title=title,
            cutline_mode="PERCENT", cutline_value=80,
            meta={"default_max_score": 100, **(
                {"removed_from_session_at": "2026-10-01T00:00:00Z"} if removed else {}
            )},
        )
        if assigned:
            apps.get_model("homework", "HomeworkAssignment").objects.create(
                tenant=self.tenant, session=self.session, enrollment=self.enrollment, homework=homework,
            )
        return homework

    def _score(self, homework=None, *, passed=True, attempt_index=1):
        return HomeworkScore.objects.create(
            homework=homework or self.homework, enrollment=self.enrollment, session=self.session,
            score=90 if passed else 0, max_score=100, passed=passed, attempt_index=attempt_index,
        )

    def _recompute(self):
        ProgressPipelineService().apply(enrollment_id=self.enrollment.id, session_id=self.session.id)

    def _assert_progress(self, expected):
        progress = SessionProgress.objects.get(enrollment=self.enrollment, session=self.session)
        self.assertEqual(progress.homework_passed, expected)
        self.assertEqual(progress.completed, expected)
        self.assertEqual(progress.attendance_type, "offline")
        self.assertEqual(progress.video_progress_rate, 93)
        self.assertFalse(progress.homework_submitted)
        self.assertEqual(
            LectureProgress.objects.get(enrollment=self.enrollment).completed_sessions, int(expected),
        )
        response = self.client.get(
            f"/api/v1/results/admin/sessions/{self.session.id}/scores/", **self.headers,
        )
        self.assertEqual(response.status_code, 200, response.data)
        row = next(row for row in response.data["rows"] if row["enrollment_id"] == self.enrollment.id)
        self.assertEqual(row["progress_completed"], expected)
        self.assertFalse(apps.get_model("submissions", "Submission").objects.filter(
            enrollment=self.enrollment,
        ).exists())

    def _patch(self, *, score=None, value=90, absent=False):
        if score is None:
            url = "/api/v1/homework/scores/quick/"
            data = {"homework_id": self.homework.id, "enrollment_id": self.enrollment.id,
                    "session_id": self.session.id, "score": value, "max_score": 100}
            if absent:
                data["meta_status"] = "NOT_SUBMITTED"
        else:
            url = f"/api/v1/homework/scores/{score.id}/"
            data = {"score": value}
            if absent:
                data["status"] = "NOT_SUBMITTED"
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.patch(url, data, format="json", **self.headers)
        self.assertEqual(response.status_code, 200, response.data)
        return response

    def test_paper_quick_score_pass_fail_and_absence_refresh_read_models(self):
        self._patch(value=90)
        self._assert_progress(True)
        self._patch(value=0)
        self._assert_progress(False)
        self._patch(value=100)
        self._assert_progress(True)
        self._patch(absent=True)
        self._assert_progress(False)

    def test_paper_detail_score_refreshes_without_online_submission(self):
        score = self._score(passed=False)
        self._patch(score=score, value=90)
        self._assert_progress(True)
        self._patch(score=score, value=0)
        self._assert_progress(False)

    def test_every_current_assignment_requires_a_passing_first_score(self):
        second = self._homework("Second")
        self._score()
        self._recompute()
        self._assert_progress(False)
        failing = self._score(second, passed=False)
        self._score(second, attempt_index=2)
        self._recompute()
        self._assert_progress(False)
        failing.passed = True
        failing.score = 90
        failing.save()
        self._recompute()
        self._assert_progress(True)

    def test_unassigned_and_removed_passes_do_not_complete_current_assignment(self):
        self._score(self._homework("Old", assigned=False))
        self._score(self._homework("Removed", removed=True))
        self._recompute()
        self._assert_progress(False)
        self._score()
        self._recompute()
        self._assert_progress(True)

    def test_legacy_scores_keep_any_pass_contract_but_removed_history_is_excluded(self):
        apps.get_model("homework", "HomeworkAssignment").objects.all().delete()
        self._score(passed=False)
        self._score(self._homework("Removed", assigned=False, removed=True))
        self._recompute()
        self._assert_progress(False)
        self._score(self._homework("Legacy", assigned=False))
        self._recompute()
        self._assert_progress(True)

    def test_cutline_and_maximum_changes_refresh_progress_in_both_directions(self):
        score = self._score()
        self._recompute()
        self._assert_progress(True)
        for cutline, maximum, expected in [(95, 100, False), (80, 100, True), (80, 200, False)]:
            with self.captureOnCommitCallbacks(execute=True), transaction.atomic():
                self.homework.cutline_value = cutline
                self.homework.meta = {**self.homework.meta, "default_max_score": maximum}
                self.homework.save()
                recalc_scores_for_homework_change(homework=self.homework)
            self._assert_progress(expected)
            score.refresh_from_db()
            self.assertEqual(score.score, 90)
            self.assertIsNone(score.reviewed_submission_revision)

    def test_failed_transaction_does_not_publish_progress(self):
        with self.captureOnCommitCallbacks(execute=True) as callbacks:
            with self.assertRaises(RuntimeError), transaction.atomic():
                response = self.client.patch(
                    "/api/v1/homework/scores/quick/",
                    {"homework_id": self.homework.id, "enrollment_id": self.enrollment.id,
                     "session_id": self.session.id, "score": 100, "max_score": 100},
                    format="json", **self.headers,
                )
                self.assertEqual(response.status_code, 200, response.data)
                raise RuntimeError("rollback score edit")
        self.assertEqual(callbacks, [])
        self.assertFalse(HomeworkScore.objects.filter(enrollment=self.enrollment).exists())
        self._assert_progress(False)
