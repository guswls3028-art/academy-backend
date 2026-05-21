from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIRequestFactory

from academy.adapters.db.django.repositories_video import (
    job_complete,
    job_mark_dead,
    job_mark_dead_if_active,
)
from apps.core.models import Tenant
from apps.domains.video.models import Video, VideoTranscodeJob
from apps.domains.video.views.internal_views import VideoProcessingCompleteView, VideoScanStuckView
from apps.domains.video.views.video_views import VideoViewSet


class VideoJobFailureStatusTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(
            name="Video Failure Tenant",
            code="video-failure",
            is_active=True,
        )
        self.video = Video.objects.create(
            tenant=self.tenant,
            title="Video",
            status=Video.Status.PROCESSING,
        )
        self.job = VideoTranscodeJob.objects.create(
            tenant=self.tenant,
            video=self.video,
            state=VideoTranscodeJob.State.RUNNING,
            attempt_count=1,
            aws_batch_job_id="aws-old",
        )
        self.video.current_job = self.job
        self.video.save(update_fields=["current_job"])

    @patch("apps.domains.video.redis_status_cache.delete_video_progress_key")
    @patch("apps.domains.video.redis_status_cache.cache_video_status")
    def test_job_mark_dead_caches_failed_terminal_status(self, mock_cache_status, mock_delete_progress):
        ok = job_mark_dead(str(self.job.id), error_code="TEST_DEAD", error_message="encoder failed")

        self.assertTrue(ok)
        self.video.refresh_from_db()
        self.job.refresh_from_db()
        self.assertEqual(self.video.status, Video.Status.FAILED)
        self.assertEqual(self.job.state, VideoTranscodeJob.State.DEAD)
        mock_cache_status.assert_called_once_with(
            tenant_id=self.tenant.id,
            video_id=self.video.id,
            status=Video.Status.FAILED,
            hls_path=None,
            duration=None,
            error_reason="encoder failed",
            ttl=None,
        )
        mock_delete_progress.assert_called_once_with(self.tenant.id, self.video.id)


@override_settings(LAMBDA_INTERNAL_API_KEY="test-internal", INTERNAL_API_ALLOW_IPS="")
class VideoProcessingCompleteViewTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.tenant = Tenant.objects.create(
            name="Video Complete Tenant",
            code="video-complete",
            is_active=True,
        )
        self.video = Video.objects.create(
            tenant=self.tenant,
            title="Video",
            status=Video.Status.PROCESSING,
        )
        self.job = VideoTranscodeJob.objects.create(
            tenant=self.tenant,
            video=self.video,
            state=VideoTranscodeJob.State.RUNNING,
            attempt_count=1,
            aws_batch_job_id="aws-current",
        )
        self.video.current_job = self.job
        self.video.save(update_fields=["current_job"])

    def _post_complete(self, data):
        request = self.factory.post(
            f"/api/v1/internal/videos/{self.video.id}/processing-complete/",
            data,
            format="json",
            HTTP_X_INTERNAL_KEY="test-internal",
        )
        view = VideoProcessingCompleteView.as_view()
        return view(request, video_id=self.video.id)

    def test_rejects_missing_job_id(self):
        response = self._post_complete({"hls_path": "videos/hls/master.m3u8", "duration": 12})

        self.assertEqual(response.status_code, 400, response.data)
        self.video.refresh_from_db()
        self.assertNotEqual(self.video.status, Video.Status.READY)

    def test_rejects_stale_non_current_job_id(self):
        stale_job = VideoTranscodeJob.objects.create(
            tenant=self.tenant,
            video=self.video,
            state=VideoTranscodeJob.State.DEAD,
            attempt_count=1,
            aws_batch_job_id="aws-stale",
        )

        response = self._post_complete(
            {
                "job_id": str(stale_job.id),
                "hls_path": "videos/hls/master.m3u8",
                "duration": 12,
            }
        )

        self.assertEqual(response.status_code, 409, response.data)
        self.video.refresh_from_db()
        self.assertNotEqual(self.video.status, Video.Status.READY)

    @patch("apps.domains.video.redis_status_cache.delete_video_progress_key")
    @patch("apps.domains.video.redis_status_cache.cache_video_status")
    def test_current_job_id_completes_through_job_complete(self, mock_cache_status, mock_delete_progress):
        response = self._post_complete(
            {
                "job_id": str(self.job.id),
                "hls_path": "videos/hls/master.m3u8",
                "duration": 12,
            }
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.video.refresh_from_db()
        self.job.refresh_from_db()
        self.assertEqual(self.video.status, Video.Status.READY)
        self.assertEqual(self.video.hls_path, "videos/hls/master.m3u8")
        self.assertEqual(self.job.state, VideoTranscodeJob.State.SUCCEEDED)
        mock_cache_status.assert_called_once()
        mock_delete_progress.assert_called_once_with(self.tenant.id, self.video.id)

    @patch("apps.domains.video.redis_status_cache.delete_video_progress_key")
    @patch("apps.domains.video.redis_status_cache.cache_video_status")
    def test_stale_job_complete_does_not_mark_video_ready(self, mock_cache_status, mock_delete_progress):
        self.video.current_job = None
        self.video.save(update_fields=["current_job"])

        ok, reason = job_complete(
            str(self.job.id),
            "videos/hls/stale-master.m3u8",
            12,
            thumbnail_r2_key="videos/thumb.jpg",
        )

        self.assertFalse(ok)
        self.assertEqual(reason, "stale_job")
        self.video.refresh_from_db()
        self.job.refresh_from_db()
        self.assertEqual(self.video.status, Video.Status.PROCESSING)
        self.assertEqual(self.video.hls_path, "")
        self.assertEqual(self.job.state, VideoTranscodeJob.State.RUNNING)
        mock_cache_status.assert_not_called()
        mock_delete_progress.assert_not_called()

    @patch("apps.domains.video.redis_status_cache.delete_video_progress_key")
    @patch("apps.domains.video.redis_status_cache.cache_video_status")
    def test_job_mark_dead_if_active_caches_failed_terminal_status(self, mock_cache_status, mock_delete_progress):
        ok, rows = job_mark_dead_if_active(
            str(self.job.id),
            error_code="TEST_DEAD",
            error_message="dlq dead",
        )

        self.assertTrue(ok)
        self.assertEqual(rows, 1)
        self.video.refresh_from_db()
        self.job.refresh_from_db()
        self.assertEqual(self.video.status, Video.Status.FAILED)
        self.assertEqual(self.job.state, VideoTranscodeJob.State.DEAD)
        mock_cache_status.assert_called_once_with(
            tenant_id=self.tenant.id,
            video_id=self.video.id,
            status=Video.Status.FAILED,
            hls_path=None,
            duration=None,
            error_reason="dlq dead",
            ttl=None,
        )
        mock_delete_progress.assert_called_once_with(self.tenant.id, self.video.id)


class ScanStuckVideoJobsTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(
            name="Video Scan Tenant",
            code="video-scan",
            is_active=True,
        )
        self.video = Video.objects.create(
            tenant=self.tenant,
            title="Stuck Video",
            status=Video.Status.PROCESSING,
        )
        self.job = VideoTranscodeJob.objects.create(
            tenant=self.tenant,
            video=self.video,
            state=VideoTranscodeJob.State.RUNNING,
            attempt_count=1,
            aws_batch_job_id="aws-old",
            last_heartbeat_at=timezone.now() - timedelta(minutes=30),
        )
        self.video.current_job = self.job
        self.video.save(update_fields=["current_job"])

    @patch(
        "apps.domains.video.management.commands.scan_stuck_video_jobs.submit_batch_job",
        return_value=("aws-new", None),
    )
    @patch("apps.domains.video.services.batch_submit.terminate_video_job", return_value=True)
    def test_stuck_scan_terminates_existing_batch_before_resubmit(self, mock_terminate, mock_submit):
        call_command("scan_stuck_video_jobs", threshold=1)

        self.job.refresh_from_db()
        mock_terminate.assert_called_once_with(str(self.job.id), reason="stuck_resubmit")
        mock_submit.assert_called_once_with(str(self.job.id), duration_seconds=None)
        self.assertEqual(self.job.state, VideoTranscodeJob.State.RETRY_WAIT)
        self.assertEqual(self.job.attempt_count, 2)
        self.assertEqual(self.job.aws_batch_job_id, "aws-new")
        self.assertEqual(self.job.last_counted_failure_aws_batch_job_id, "aws-old")

    @patch(
        "apps.domains.video.management.commands.scan_stuck_video_jobs.submit_batch_job",
        return_value=("aws-new", None),
    )
    @patch("apps.domains.video.services.batch_submit.terminate_video_job", return_value=False)
    def test_stuck_scan_skips_resubmit_when_existing_batch_terminate_fails(self, mock_terminate, mock_submit):
        call_command("scan_stuck_video_jobs", threshold=1)

        self.job.refresh_from_db()
        mock_terminate.assert_called_once_with(str(self.job.id), reason="stuck_resubmit")
        mock_submit.assert_not_called()
        self.assertEqual(self.job.state, VideoTranscodeJob.State.RUNNING)
        self.assertEqual(self.job.attempt_count, 1)
        self.assertEqual(self.job.aws_batch_job_id, "aws-old")
        self.assertEqual(self.job.last_counted_failure_aws_batch_job_id, "")
        self.assertEqual(self.job.error_code, "BATCH_TERMINATE_FAILED")


class ReconcileBatchVideoJobsTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(
            name="Video Reconcile Tenant",
            code="video-reconcile",
            is_active=True,
        )
        self.video = Video.objects.create(
            tenant=self.tenant,
            title="Reconcile Video",
            status=Video.Status.PROCESSING,
        )
        self.job = VideoTranscodeJob.objects.create(
            tenant=self.tenant,
            video=self.video,
            state=VideoTranscodeJob.State.RETRY_WAIT,
            attempt_count=2,
            aws_batch_job_id="aws-old",
        )
        self.video.current_job = self.job
        self.video.save(update_fields=["current_job"])

    def _make_job_reconcileable(self):
        VideoTranscodeJob.objects.filter(pk=self.job.pk).update(updated_at=timezone.now() - timedelta(minutes=10))

    @patch("apps.domains.video.management.commands.reconcile_batch_video_jobs.Command._run_orphan_terminate")
    @patch(
        "apps.domains.video.management.commands.reconcile_batch_video_jobs.submit_batch_job",
        return_value=(None, "submit down"),
    )
    @patch(
        "apps.domains.video.management.commands.reconcile_batch_video_jobs._describe_jobs_boto3",
        return_value=[{"jobId": "aws-old", "status": "FAILED", "statusReason": "boom"}],
    )
    def test_reconcile_counts_same_failed_aws_batch_job_once(self, mock_describe, mock_submit, _mock_orphans):
        self._make_job_reconcileable()

        call_command("reconcile_batch_video_jobs", older_than_minutes=1, skip_lock=True)

        self.job.refresh_from_db()
        self.assertEqual(self.job.state, VideoTranscodeJob.State.RETRY_WAIT)
        self.assertEqual(self.job.attempt_count, 3)
        self.assertEqual(self.job.last_counted_failure_aws_batch_job_id, "aws-old")

        self._make_job_reconcileable()
        call_command("reconcile_batch_video_jobs", older_than_minutes=1, skip_lock=True)

        self.job.refresh_from_db()
        self.assertEqual(self.job.state, VideoTranscodeJob.State.RETRY_WAIT)
        self.assertEqual(self.job.attempt_count, 3)
        self.assertEqual(self.job.last_counted_failure_aws_batch_job_id, "aws-old")
        self.assertEqual(mock_describe.call_count, 2)
        self.assertEqual(mock_submit.call_count, 2)


class InternalVideoScanStuckViewTests(TestCase):
    @override_settings(LAMBDA_INTERNAL_API_KEY="test-internal-key", INTERNAL_API_ALLOW_IPS="")
    @patch("django.core.management.call_command")
    def test_internal_scan_stuck_endpoint_delegates_to_management_command(self, mock_call_command):
        request = APIRequestFactory().post(
            "/api/v1/internal/video/scan-stuck/",
            {"threshold": 7, "dry_run": True},
            format="json",
            HTTP_X_INTERNAL_KEY="test-internal-key",
        )
        response = VideoScanStuckView.as_view()(request)

        self.assertEqual(response.status_code, 200, response.data)
        mock_call_command.assert_called_once()
        args, kwargs = mock_call_command.call_args
        self.assertEqual(args[0], "scan_stuck_video_jobs")
        self.assertEqual(kwargs["threshold"], 7)
        self.assertTrue(kwargs["dry_run"])
        self.assertIn("stdout", kwargs)

    @override_settings(LAMBDA_INTERNAL_API_KEY="test-internal-key", INTERNAL_API_ALLOW_IPS="")
    @patch("django.core.management.call_command")
    def test_internal_scan_stuck_endpoint_rejects_invalid_threshold(self, mock_call_command):
        request = APIRequestFactory().post(
            "/api/v1/internal/video/scan-stuck/",
            {"threshold": "bad"},
            format="json",
            HTTP_X_INTERNAL_KEY="test-internal-key",
        )
        response = VideoScanStuckView.as_view()(request)

        self.assertEqual(response.status_code, 400)
        mock_call_command.assert_not_called()


class VideoUploadCompleteStatusCacheTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(
            name="Video Upload Tenant",
            code="video-upload",
            is_active=True,
        )
        self.video = Video.objects.create(
            tenant=self.tenant,
            title="Upload Video",
            status=Video.Status.PENDING,
            file_key="tenant/video/raw.mp4",
        )

    @patch("apps.domains.video.redis_status_cache.cache_video_status")
    @patch(
        "apps.domains.video.views.video_views.create_job_and_submit_batch",
        return_value=SimpleNamespace(job=None, reject_reason="tenant_limit"),
    )
    @patch(
        "apps.domains.video.views.video_views._validate_source_media_via_ffprobe",
        return_value=(True, {"duration": 60}, ""),
    )
    @patch("apps.domains.video.views.video_views.create_presigned_get_url", return_value="https://example.test/raw.mp4")
    @patch("apps.domains.video.views.video_views.head_object", return_value=(True, 1024))
    def test_upload_complete_caches_uploaded_status_before_worker_starts(
        self,
        mock_head_object,
        mock_presign,
        mock_probe,
        mock_enqueue,
        mock_cache_status,
    ):
        response = VideoViewSet()._upload_complete_impl(self.video)

        self.assertEqual(response.status_code, 200, response.data)
        self.video.refresh_from_db()
        self.assertEqual(self.video.status, Video.Status.UPLOADED)
        self.assertEqual(self.video.duration, 60)
        mock_cache_status.assert_called_once_with(
            tenant_id=self.tenant.id,
            video_id=self.video.id,
            status=Video.Status.UPLOADED,
            duration=60,
            ttl=21600,
        )
        mock_head_object.assert_called_once()
        mock_presign.assert_called_once()
        mock_probe.assert_called_once()
        mock_enqueue.assert_called_once()
