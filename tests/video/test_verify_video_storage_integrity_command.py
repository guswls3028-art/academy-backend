from __future__ import annotations

from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings

from apps.core.models import Tenant
from apps.domains.video.models import Video


@override_settings(R2_VIDEO_BUCKET="video-test")
class VerifyVideoStorageIntegrityCommandTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(
            name="Video integrity",
            code="video-integrity",
            is_active=True,
        )
        self.videos = [
            Video.objects.create(
                tenant=self.tenant,
                title=f"Ready {index}",
                status=Video.Status.READY,
                hls_path=f"tenant/{self.tenant.id}/video/{index}/master.m3u8",
            )
            for index in range(1, 4)
        ]

    @patch(
        "apps.domains.video.management.commands.verify_video_storage_integrity._count_segments",
        return_value=2,
    )
    @patch(
        "apps.domains.video.management.commands.verify_video_storage_integrity._head_exists",
        return_value=True,
    )
    def test_cursor_batch_reports_exact_continuation(self, head_exists, _count_segments):
        output = StringIO()

        call_command(
            "verify_video_storage_integrity",
            "--limit",
            "2",
            stdout=output,
        )

        self.assertEqual(head_exists.call_count, 2)
        self.assertIn(
            f"BATCH: checked=2 after_id=0 last_id={self.videos[1].id} has_more=true",
            output.getvalue(),
        )

    @patch(
        "apps.domains.video.management.commands.verify_video_storage_integrity._head_exists",
        side_effect=TimeoutError("R2 timeout"),
    )
    def test_transport_failure_fails_closed(self, _head_exists):
        with self.assertRaisesMessage(CommandError, "R2 verification failed"):
            call_command("verify_video_storage_integrity", "--limit", "1")

    @patch(
        "apps.domains.video.management.commands.verify_video_storage_integrity._head_exists",
        return_value=False,
    )
    def test_missing_master_fails_command(self, _head_exists):
        with self.assertRaisesMessage(
            CommandError,
            "video_storage_integrity_failed:corrupted=1",
        ):
            call_command("verify_video_storage_integrity", "--limit", "1")
