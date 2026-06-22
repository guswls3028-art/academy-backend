from __future__ import annotations

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from apps.core.models import Tenant
from apps.domains.submissions.models import Submission
from apps.domains.submissions.omr_pipeline.services.state_recovery import (
    recover_stuck_submissions,
)


class StateRecoveryTransitionTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(
            name="State Recovery Tenant",
            code="state-recovery",
            is_active=True,
        )
        self.user = get_user_model().objects.create_user(
            username="state_recovery_staff",
            password="test1234",
            tenant=self.tenant,
            is_staff=True,
        )

    def _make_submission(self, *, status: str, updated_minutes_ago: int) -> Submission:
        submission = Submission.objects.create(
            tenant=self.tenant,
            user=self.user,
            target_type=Submission.TargetType.EXAM,
            target_id=1,
            source=Submission.Source.OMR_SCAN,
            status=status,
            file_key="omr/state-recovery.jpg",
        )
        Submission.objects.filter(id=submission.id).update(
            updated_at=timezone.now() - timedelta(minutes=updated_minutes_ago)
        )
        submission.refresh_from_db()
        return submission

    def test_recovers_stuck_extracting_through_transition_guard(self):
        stuck = self._make_submission(
            status=Submission.Status.EXTRACTING,
            updated_minutes_ago=45,
        )

        report = recover_stuck_submissions(actor="test")

        self.assertIn(stuck.id, report.recovered)
        self.assertEqual(report.failed_transitions, [])
        stuck.refresh_from_db()
        self.assertEqual(stuck.status, Submission.Status.FAILED)
        self.assertEqual(stuck.error_message, "stuck:extracting_timeout")
        self.assertEqual(
            (stuck.meta or {}).get("state_recovery", {}).get("from_status"),
            Submission.Status.EXTRACTING,
        )
