from __future__ import annotations

from datetime import timedelta
from io import StringIO
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.core.management import call_command
from django.test import SimpleTestCase
from django.utils import timezone

from apps.domains.video.management.commands.reconcile_batch_video_jobs import (
    Command as ReconcileCommand,
)
from apps.domains.video.models import Video, VideoTranscodeJob


class DetectStuckVideosCommandTests(SimpleTestCase):
    def _run_command(self, *, dry_run: bool) -> tuple[str, MagicMock]:
        active_jobs = MagicMock()
        active_jobs.values_list.return_value = []

        stale_jobs = MagicMock()
        stale_jobs.select_related.return_value = []

        orphan_videos = MagicMock()
        orphan_videos.exclude.return_value = orphan_videos
        orphan_videos.filter.return_value = orphan_videos
        orphan_videos.select_related.return_value = [
            SimpleNamespace(
                id=123,
                session=None,
                title="stuck video",
                updated_at=timezone.now() - timedelta(hours=2),
                status=Video.Status.UPLOADED,
            )
        ]

        output = StringIO()
        with (
            patch(
                "apps.domains.video.management.commands.detect_stuck_videos.VideoTranscodeJob.objects.filter",
                side_effect=[active_jobs, stale_jobs],
            ),
            patch(
                "apps.domains.video.management.commands.detect_stuck_videos.Video.objects.filter",
                return_value=orphan_videos,
            ),
            patch("apps.domains.video.services.ops_events.emit_ops_event") as emit_ops_event,
        ):
            command_args = ["detect_stuck_videos"]
            if dry_run:
                command_args.append("--dry-run")
            call_command(*command_args, stdout=output)

        return output.getvalue(), emit_ops_event

    def test_dry_run_does_not_persist_detection_event(self):
        output, emit_ops_event = self._run_command(dry_run=True)

        emit_ops_event.assert_not_called()
        self.assertIn("Total: stuck=1 repaired=0 (dry-run)", output)

    def test_normal_detection_still_persists_detection_event(self):
        output, emit_ops_event = self._run_command(dry_run=False)

        emit_ops_event.assert_called_once_with(
            "VIDEO_STUCK_DETECTED",
            severity="WARNING",
            payload={"stuck_count": 1, "repaired_count": 0},
        )
        self.assertIn("Total: stuck=1 repaired=0", output)


class ReconcileBatchVideoJobsDryRunTests(SimpleTestCase):
    def _querysets_for(self, jobs: list[SimpleNamespace]):
        duplicate_groups = MagicMock()
        duplicate_groups.values.return_value.annotate.return_value.filter.return_value = []

        job_queryset = MagicMock()
        sliced = (
            job_queryset.exclude.return_value.filter.return_value.select_related.return_value
            .order_by.return_value.__getitem__
        )
        sliced.return_value = jobs
        return [duplicate_groups, job_queryset]

    def _job(self) -> SimpleNamespace:
        return SimpleNamespace(
            id="00000000-0000-0000-0000-000000000123",
            aws_batch_job_id="aws-job",
            state=VideoTranscodeJob.State.QUEUED,
            created_at=timezone.now() - timedelta(minutes=10),
            video_id=123,
            tenant_id=456,
        )

    @patch(
        "apps.domains.video.management.commands.reconcile_batch_video_jobs.Command._run_reconcile"
    )
    @patch(
        "apps.domains.video.management.commands.reconcile_batch_video_jobs._release_reconcile_lock"
    )
    @patch(
        "apps.domains.video.management.commands.reconcile_batch_video_jobs._acquire_reconcile_lock"
    )
    def test_dry_run_does_not_mutate_coordination_lock(
        self,
        acquire_lock: MagicMock,
        release_lock: MagicMock,
        run_reconcile: MagicMock,
    ):
        with self.assertLogs(
            "apps.domains.video.management.commands.reconcile_batch_video_jobs",
            level="INFO",
        ) as logs:
            call_command("reconcile_batch_video_jobs", "--dry-run")

        acquire_lock.assert_not_called()
        release_lock.assert_not_called()
        run_reconcile.assert_called_once()
        self.assertTrue(
            any("dry-run starting without coordination lock" in line for line in logs.output)
        )
        self.assertFalse(any("lock acquired" in line for line in logs.output))

    def test_dry_run_describe_failure_does_not_persist_ops_event(self):
        job = self._job()
        with (
            patch.object(
                VideoTranscodeJob.objects,
                "filter",
                side_effect=self._querysets_for([job]),
            ),
            patch(
                "apps.domains.video.management.commands.reconcile_batch_video_jobs._describe_jobs_boto3",
                side_effect=RuntimeError("describe failed"),
            ),
            patch("apps.domains.video.services.ops_events.emit_ops_event") as emit_ops_event,
        ):
            ReconcileCommand()._run_reconcile(True, False, timezone.now())

        emit_ops_event.assert_not_called()

    @patch(
        "apps.domains.video.management.commands.reconcile_batch_video_jobs.Command._run_orphan_terminate"
    )
    def test_dry_run_reads_but_does_not_mutate_not_found_counter(self, _run_orphans):
        job = self._job()
        with (
            patch.object(
                VideoTranscodeJob.objects,
                "filter",
                side_effect=self._querysets_for([job]),
            ),
            patch(
                "apps.domains.video.management.commands.reconcile_batch_video_jobs._describe_jobs_boto3",
                return_value=[],
            ),
            patch(
                "apps.domains.video.management.commands.reconcile_batch_video_jobs._get_not_found_count",
                return_value=2,
            ) as get_count,
            patch(
                "apps.domains.video.management.commands.reconcile_batch_video_jobs._incr_not_found_count"
            ) as increment_count,
        ):
            ReconcileCommand()._run_reconcile(True, False, timezone.now())

        get_count.assert_called_once_with(str(job.id))
        increment_count.assert_not_called()

    @patch(
        "apps.domains.video.management.commands.reconcile_batch_video_jobs.Command._run_orphan_terminate"
    )
    def test_dry_run_does_not_reset_not_found_counter(self, _run_orphans):
        job = self._job()
        with (
            patch.object(
                VideoTranscodeJob.objects,
                "filter",
                side_effect=self._querysets_for([job]),
            ),
            patch(
                "apps.domains.video.management.commands.reconcile_batch_video_jobs._describe_jobs_boto3",
                return_value=[{"jobId": "aws-job", "status": "RUNNING"}],
            ),
            patch(
                "apps.domains.video.management.commands.reconcile_batch_video_jobs._reset_not_found_count"
            ) as reset_count,
        ):
            ReconcileCommand()._run_reconcile(True, False, timezone.now())

        reset_count.assert_not_called()
