from __future__ import annotations

from datetime import timedelta
from io import StringIO
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.core.management import call_command
from django.test import SimpleTestCase
from django.utils import timezone

from apps.domains.video.models import Video


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
