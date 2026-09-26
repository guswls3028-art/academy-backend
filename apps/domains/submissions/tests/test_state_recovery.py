from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from io import StringIO
from unittest.mock import patch

from django.apps import apps as django_apps
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from apps.core.models import Tenant
from apps.domains.submissions.models import Submission
from apps.domains.submissions.omr_pipeline.services.state_recovery import (
    detect_stuck_submissions,
    recover_stuck_submissions,
)
from apps.domains.submissions.services.lifecycle import STUCK_RECOVERABLE_STATUSES


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

    def test_live_ai_lease_defers_recovery_until_worker_window_expires(self):
        AIJobModel = django_apps.get_model("ai_domain", "AIJobModel")
        stuck = self._make_submission(
            status=Submission.Status.DISPATCHED, updated_minutes_ago=45,
        )
        now = timezone.now()
        job = AIJobModel.objects.create(
            job_id=f"recovery-live-{stuck.id}",
            job_type="omr_grading",
            status="RUNNING",
            tenant_id=str(self.tenant.id),
            source_domain="submissions",
            source_id=str(stuck.id),
            started_at=now - timedelta(minutes=40),
            lease_expires_at=now + timedelta(minutes=19),
        )

        report = recover_stuck_submissions(actor="test")
        self.assertIn(stuck.id, report.skipped)
        self.assertNotIn(stuck.id, report.recovered)
        stuck.refresh_from_db()
        self.assertEqual(stuck.status, Submission.Status.DISPATCHED)

        AIJobModel.objects.filter(pk=job.pk).update(
            started_at=now - timedelta(minutes=70),
            lease_expires_at=now - timedelta(minutes=5),
        )
        report = recover_stuck_submissions(actor="test")
        self.assertIn(stuck.id, report.recovered)
        stuck.refresh_from_db()
        self.assertEqual(stuck.status, Submission.Status.FAILED)

    def test_foreign_ai_lease_cannot_hold_this_tenant_submission(self):
        AIJobModel = django_apps.get_model("ai_domain", "AIJobModel")
        stuck = self._make_submission(
            status=Submission.Status.DISPATCHED, updated_minutes_ago=45,
        )
        foreign_tenant = Tenant.objects.create(
            name="Foreign Recovery Tenant", code="foreign-state-recovery", is_active=True,
        )
        AIJobModel.objects.create(
            job_id=f"foreign-recovery-{stuck.id}",
            job_type="omr_grading",
            status="RUNNING",
            tenant_id=str(foreign_tenant.id),
            source_domain="submissions",
            source_id=str(stuck.id),
            started_at=timezone.now(),
            lease_expires_at=timezone.now() + timedelta(minutes=30),
        )

        report = recover_stuck_submissions(actor="test")
        self.assertIn(stuck.id, report.recovered)

    def test_dispatched_reconciler_ignores_foreign_job_for_same_submission_id(self):
        AIJobModel = django_apps.get_model("ai_domain", "AIJobModel")
        stuck = self._make_submission(
            status=Submission.Status.DISPATCHED, updated_minutes_ago=45,
        )
        foreign_tenant = Tenant.objects.create(
            name="Foreign Reconcile Tenant", code="foreign-reconcile", is_active=True,
        )
        foreign_job = AIJobModel.objects.create(
            job_id=f"foreign-dispatched-{stuck.id}",
            job_type="omr_grading",
            status="DONE",
            tenant_id=str(foreign_tenant.id),
            source_domain="submissions",
            source_id=str(stuck.id),
        )

        output = StringIO()
        call_command("reconcile_dispatched_submissions", detect_only=True, stdout=output)
        self.assertIn("no_ai_job=1", output.getvalue())
        self.assertNotIn(foreign_job.job_id, output.getvalue())

        own_job = AIJobModel.objects.create(
            job_id=f"own-dispatched-{stuck.id}",
            job_type="omr_grading",
            status="DONE",
            tenant_id=str(self.tenant.id),
            source_domain="submissions",
            source_id=str(stuck.id),
        )
        output = StringIO()
        call_command("reconcile_dispatched_submissions", detect_only=True, stdout=output)
        self.assertIn("recoverable=1", output.getvalue())
        self.assertIn(own_job.job_id, output.getvalue())
        self.assertNotIn(foreign_job.job_id, output.getvalue())

    def test_rechecks_each_status_version_after_detection(self):
        timeouts = {
            status: (index + 1) * 10
            for index, status in enumerate(STUCK_RECOVERABLE_STATUSES)
        }
        submissions = {
            status: self._make_submission(
                status=status,
                updated_minutes_ago=minutes + 20,
            )
            for status, minutes in timeouts.items()
        }

        def detect_then_advance_stale_versions(**kwargs):
            alerts = detect_stuck_submissions(**kwargs)
            for status, submission in submissions.items():
                Submission.objects.filter(pk=submission.pk).update(
                    updated_at=timezone.now()
                    - timedelta(minutes=timeouts[status] + 5)
                )
            return alerts

        with patch(
            "apps.domains.submissions.omr_pipeline.services.state_recovery."
            "detect_stuck_submissions",
            side_effect=detect_then_advance_stale_versions,
        ):
            report = recover_stuck_submissions(actor="test", timeouts=timeouts)

        self.assertEqual(report.recovered, [])
        self.assertCountEqual(
            report.skipped,
            [submission.pk for submission in submissions.values()],
        )
        for status, submission in submissions.items():
            submission.refresh_from_db()
            self.assertEqual(submission.status, status)
            self.assertNotIn("state_recovery", submission.meta or {})

    def test_rechecks_each_status_cutoff_after_detection(self):
        timeouts = {
            status: (index + 1) * 10
            for index, status in enumerate(STUCK_RECOVERABLE_STATUSES)
        }
        submissions = {
            status: self._make_submission(
                status=status,
                updated_minutes_ago=minutes + 20,
            )
            for status, minutes in timeouts.items()
        }

        def detect_then_refresh_candidates(**kwargs):
            alerts = detect_stuck_submissions(**kwargs)
            refreshed_at_by_status = {}
            for status, submission in submissions.items():
                refreshed_at = timezone.now() - timedelta(
                    minutes=timeouts[status] - 1
                )
                Submission.objects.filter(pk=submission.pk).update(
                    updated_at=refreshed_at
                )
                refreshed_at_by_status[status] = refreshed_at
            return [
                replace(alert, updated_at=refreshed_at_by_status[alert.status])
                for alert in alerts
            ]

        with patch(
            "apps.domains.submissions.omr_pipeline.services.state_recovery."
            "detect_stuck_submissions",
            side_effect=detect_then_refresh_candidates,
        ):
            report = recover_stuck_submissions(actor="test", timeouts=timeouts)

        self.assertEqual(report.recovered, [])
        self.assertCountEqual(
            report.skipped,
            [submission.pk for submission in submissions.values()],
        )
        for status, submission in submissions.items():
            submission.refresh_from_db()
            self.assertEqual(submission.status, status)
            self.assertNotIn("state_recovery", submission.meta or {})

    def test_done_and_failed_submissions_remain_unchanged(self):
        done = self._make_submission(
            status=Submission.Status.DONE,
            updated_minutes_ago=60,
        )
        failed = self._make_submission(
            status=Submission.Status.FAILED,
            updated_minutes_ago=60,
        )
        Submission.objects.filter(pk=failed.pk).update(
            error_message="worker failure",
            meta={"worker_failure": True},
        )

        report = recover_stuck_submissions(actor="test")

        self.assertEqual(report.detected, [])
        self.assertEqual(report.recovered, [])
        self.assertEqual(report.skipped, [])
        done.refresh_from_db()
        failed.refresh_from_db()
        self.assertEqual(done.status, Submission.Status.DONE)
        self.assertEqual(done.meta, None)
        self.assertEqual(failed.status, Submission.Status.FAILED)
        self.assertEqual(failed.error_message, "worker failure")
        self.assertEqual(failed.meta, {"worker_failure": True})

    def test_repeated_scheduled_command_is_idempotent(self):
        stuck = self._make_submission(
            status=Submission.Status.EXTRACTING,
            updated_minutes_ago=45,
        )
        first_out = StringIO()
        second_out = StringIO()

        call_command(
            "recover_stuck_omr_submissions",
            skip_late_ai_answer_recovery=True,
            stdout=first_out,
        )
        stuck.refresh_from_db()
        first_meta = stuck.meta
        first_updated_at = stuck.updated_at
        call_command(
            "recover_stuck_omr_submissions",
            skip_late_ai_answer_recovery=True,
            stdout=second_out,
        )

        stuck.refresh_from_db()
        self.assertIn("detected=1 recovered=1", first_out.getvalue())
        self.assertIn("detected=0 recovered=0", second_out.getvalue())
        self.assertEqual(stuck.status, Submission.Status.FAILED)
        self.assertEqual(stuck.meta, first_meta)
        self.assertEqual(stuck.updated_at, first_updated_at)
