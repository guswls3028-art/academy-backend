from __future__ import annotations

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.core.models import Tenant
from apps.domains.results.tasks.grading_tasks import grade_submission_task
from apps.domains.submissions.models import Submission


User = get_user_model()


class GradingTaskFailureTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(code="grading-task", name="Grading Task")
        self.user = User.objects.create_user(
            username="grading-task-user",
            password="pass1234!",
            tenant=self.tenant,
        )

    def test_failure_marks_answers_ready_submission_failed(self):
        submission = Submission.objects.create(
            tenant=self.tenant,
            user=self.user,
            target_type=Submission.TargetType.EXAM,
            target_id=12345,
            source=Submission.Source.OMR_SCAN,
            status=Submission.Status.ANSWERS_READY,
        )

        with patch(
            "apps.domains.results.services.grading_service.grade_submission",
            side_effect=RuntimeError("boom"),
        ):
            payload = grade_submission_task(int(submission.id))

        submission.refresh_from_db()
        self.assertFalse(payload["ok"])
        self.assertTrue(payload["failed_marked"])
        self.assertEqual(submission.status, Submission.Status.FAILED)
        self.assertEqual(submission.error_message, "grading failed - see logs")
