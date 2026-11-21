from __future__ import annotations

from datetime import timedelta
from io import StringIO
import os
from types import SimpleNamespace
from unittest.mock import DEFAULT, Mock, patch
from uuid import uuid4

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIRequestFactory

from academy.adapters.db.django.repositories_video import (
    job_complete,
    job_mark_dead,
    job_mark_dead_if_active,
    job_set_running,
)
from apps.core.models import Tenant
from apps.domains.video.models import Video, VideoTranscodeJob
from apps.domains.video.serializers import VideoDetailSerializer
from apps.domains.video.services.video_encoding import REASON_SUBMIT_FAILED
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


    @patch("apps.domains.video.redis_status_cache.delete_video_progress_key")
    @patch("apps.domains.video.redis_status_cache.cache_video_status")
    def test_permanent_failure_only_counts_the_current_uncancelled_batch_attempt(self, _, __):
        def mark_dead(batch_id):
            return job_mark_dead(
                str(self.job.id),
                error_code="SOURCE_NO_VIDEO_STREAM",
                error_message="Source file contains no video stream.",
                failed_aws_batch_job_id=batch_id,
            )

        self.assertFalse(mark_dead("stale-batch"))
        self.job.cancel_requested = True
        self.job.save(update_fields=["cancel_requested"])
        self.assertFalse(mark_dead("aws-old"))
        self.job.cancel_requested = False
        self.job.save(update_fields=["cancel_requested"])
        self.assertTrue(mark_dead("aws-old"))
        self.assertFalse(mark_dead("aws-old"))
        self.job.refresh_from_db()
        self.video.refresh_from_db()
        self.assertEqual(self.job.attempt_count, 2)
        self.assertEqual(self.job.last_counted_failure_aws_batch_job_id, "aws-old")
        self.assertEqual(self.job.error_code, "SOURCE_NO_VIDEO_STREAM")
        self.assertEqual(self.job.state, VideoTranscodeJob.State.DEAD)
        self.assertEqual(self.video.status, Video.Status.FAILED)


    def test_batch_worker_records_ffmpeg_diagnostics_and_skips_retry_for_audio_only_source(self):
        from academy.adapters.video.transcoder import FFmpegProcessError

        # The test runner already initialized Django and its process-wide logging.
        with patch("django.setup"), patch("logging.basicConfig"):
            from apps.worker.video_worker import batch_main

        self.job.state = VideoTranscodeJob.State.QUEUED
        self.job.save(update_fields=["state"])
        diagnostics = ("Stream map '0:V:0' matches no streams.",)
        with (
            patch.dict(os.environ, VIDEO_JOB_ID=str(self.job.id), AWS_BATCH_JOB_ID="aws-old"),
            patch("django.db.close_old_connections"),  # The test owns a shared transaction.
            patch("apps.domains.video.redis_status_cache.delete_video_progress_key"),
            patch("apps.domains.video.redis_status_cache.cache_video_status"),
            patch.multiple(
                batch_main,
                signal=SimpleNamespace(),
                threading=SimpleNamespace(Thread=Mock()),
                _heartbeat_stop=Mock(),
                _current_job_id=[None],
                load_config=DEFAULT,
                RedisProgressAdapter=DEFAULT,
                cache_video_status=DEFAULT,
                _log_json=DEFAULT,
                job_fail_retry=DEFAULT,
                process_video=Mock(side_effect=FFmpegProcessError(1, diagnostics, invalid_source=True)),
            ) as mocks,
        ):
            self.assertEqual(batch_main.main(), 1)
            mocks["job_fail_retry"].assert_not_called()
            mocks["_log_json"].assert_any_call(
                "BATCH_JOB_FAILED",
                job_id=str(self.job.id),
                tenant_id=self.tenant.id,
                video_id=self.video.id,
                aws_batch_job_id="aws-old",
                error_type="ffmpeg",
                exit_code=1,
                diagnostics=diagnostics,
                retryable=False,
            )

        self.job.refresh_from_db()
        self.video.refresh_from_db()
        self.assertEqual(self.job.state, VideoTranscodeJob.State.DEAD)
        self.assertEqual(self.job.attempt_count, 2)
        self.assertEqual(self.video.status, Video.Status.FAILED)
        self.assertIn("영상이 포함된 원본을 다시 업로드", self.video.error_reason)


class VideoJobRunningStatusTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(
            name="Video Running Tenant",
            code="video-running",
            is_active=True,
        )
        self.video = Video.objects.create(
            tenant=self.tenant,
            title="Queued Video",
            status=Video.Status.UPLOADED,
        )
        self.job = VideoTranscodeJob.objects.create(
            tenant=self.tenant,
            video=self.video,
            state=VideoTranscodeJob.State.QUEUED,
            attempt_count=1,
            aws_batch_job_id="aws-current",
        )
        self.video.current_job = self.job
        self.video.save(update_fields=["current_job"])

    @patch("apps.domains.video.redis_status_cache.cache_video_status")
    def test_job_set_running_moves_video_to_processing(self, mock_cache_status):
        self.assertTrue(job_set_running(str(self.job.id)))

        self.video.refresh_from_db()
        self.job.refresh_from_db()
        self.assertEqual(self.job.state, VideoTranscodeJob.State.RUNNING)
        self.assertEqual(self.video.status, Video.Status.PROCESSING)
        self.assertIsNotNone(self.video.processing_started_at)
        mock_cache_status.assert_called_once_with(
            tenant_id=self.tenant.id,
            video_id=self.video.id,
            status=Video.Status.PROCESSING,
            hls_path=None,
            duration=None,
            error_reason=None,
            ttl=21600,
        )


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
        hls_path = f"tenants/{self.tenant.id}/video/hls/{self.video.id}/master.m3u8"
        response = self._post_complete(
            {
                "job_id": str(self.job.id),
                "hls_path": hls_path,
                "duration": 12,
            }
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.video.refresh_from_db()
        self.job.refresh_from_db()
        self.assertEqual(self.video.status, Video.Status.READY)
        self.assertEqual(self.video.hls_path, hls_path)
        self.assertEqual(self.job.state, VideoTranscodeJob.State.SUCCEEDED)
        mock_cache_status.assert_called_once()
        mock_delete_progress.assert_called_once_with(self.tenant.id, self.video.id)

    def test_rejects_hls_path_outside_video_tenant_prefix(self):
        response = self._post_complete(
            {
                "job_id": str(self.job.id),
                "hls_path": (
                    f"tenants/{self.tenant.id + 1}/video/hls/"
                    f"{self.video.id}/master.m3u8"
                ),
                "duration": 12,
            }
        )

        self.assertEqual(response.status_code, 409, response.data)
        self.assertEqual(response.data["detail"], "invalid_hls_path")
        self.video.refresh_from_db()
        self.job.refresh_from_db()
        self.assertEqual(self.video.status, Video.Status.PROCESSING)
        self.assertEqual(self.video.hls_path, "")
        self.assertEqual(self.job.state, VideoTranscodeJob.State.RUNNING)

    def test_rejects_thumbnail_path_outside_video_prefix(self):
        response = self._post_complete(
            {
                "job_id": str(self.job.id),
                "hls_path": (
                    f"tenants/{self.tenant.id}/video/hls/"
                    f"{self.video.id}/master.m3u8"
                ),
                "thumbnail_r2_key": (
                    f"tenants/{self.tenant.id + 1}/video/hls/"
                    f"{self.video.id}/thumbnail.jpg"
                ),
                "duration": 12,
            }
        )

        self.assertEqual(response.status_code, 409, response.data)
        self.assertEqual(response.data["detail"], "invalid_thumbnail_path")
        self.video.refresh_from_db()
        self.job.refresh_from_db()
        self.assertEqual(self.video.status, Video.Status.PROCESSING)
        self.assertEqual(self.video.thumbnail_r2_key, "")
        self.assertEqual(self.job.state, VideoTranscodeJob.State.RUNNING)

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


@override_settings(VIDEO_BATCH_JOB_QUEUE="lecture-videos", VIDEO_BATCH_JOB_DEFINITION="lecture-encode")
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

    def _targeted_host_failure(self):
        aws_id = str(uuid4())
        self.job.aws_batch_job_id = aws_id
        self.job.save(update_fields=["aws_batch_job_id", "updated_at"])
        return {
            "jobId": aws_id, "jobName": f"video-{str(self.job.id)[:8]}",
            "status": "FAILED", "statusReason": "Host EC2 (instance i-example) terminated.",
            "jobQueue": "arn:aws:batch:ap-northeast-2:123456789012:job-queue/lecture-videos",
            "jobDefinition": "arn:aws:batch:ap-northeast-2:123456789012:job-definition/lecture-encode:2",
            "parameters": {"job_id": str(self.job.id)},
        }

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

    @patch("apps.domains.video.management.commands.reconcile_batch_video_jobs.Command._run_reconcile")
    @patch("apps.domains.video.management.commands.reconcile_batch_video_jobs._release_reconcile_lock")
    @patch("apps.domains.video.management.commands.reconcile_batch_video_jobs._acquire_reconcile_lock", return_value=True)
    @patch("apps.domains.video.management.commands.reconcile_batch_video_jobs.submit_batch_job")
    @patch("apps.domains.video.management.commands.reconcile_batch_video_jobs._describe_jobs_boto3")
    def test_targeted_host_failure_recovers_recent_job_and_deduplicates_delivery(
        self, describe, submit, acquire, release, sweep,
    ):
        failed = self._targeted_host_failure()
        replacement = str(uuid4())
        describe.return_value = [failed]
        submit.return_value = (replacement, None)

        for _ in range(2):
            call_command("reconcile_batch_video_jobs", failed_batch_id=failed["jobId"])

        self.job.refresh_from_db()
        submit.assert_called_once_with(str(self.job.id), duration_seconds=None)
        acquire.assert_called_with(require_redis=True)
        self.assertEqual(release.call_count, 2)
        self.assertEqual(self.job.aws_batch_job_id, replacement)
        self.assertEqual(self.job.attempt_count, 3)
        self.assertEqual(self.job.last_counted_failure_aws_batch_job_id, failed["jobId"])
        sweep.assert_not_called()

    @patch("apps.domains.video.management.commands.reconcile_batch_video_jobs._release_reconcile_lock")
    @patch("apps.domains.video.management.commands.reconcile_batch_video_jobs._acquire_reconcile_lock", return_value=True)
    @patch("apps.domains.video.management.commands.reconcile_batch_video_jobs.submit_batch_job")
    @patch("apps.domains.video.management.commands.reconcile_batch_video_jobs._describe_jobs_boto3")
    def test_targeted_recovery_rejects_non_authoritative_and_retired_jobs(self, describe, submit, _acquire, _release):
        failed = self._targeted_host_failure()
        for field, value in (
            ("status", "RUNNING"), ("statusReason", "User cancelled job"), ("jobQueue", "other-queue"),
            ("jobDefinition", "other-jobdef"), ("jobName", "different-name"), ("jobId", str(uuid4())),
        ):
            with self.subTest(field=field):
                describe.return_value = [{**failed, field: value}]
                call_command("reconcile_batch_video_jobs", failed_batch_id=failed["jobId"])
        describe.return_value = [failed]
        for changes in (
            {"cancel_requested": True}, {"state": VideoTranscodeJob.State.SUCCEEDED}, {"aws_batch_job_id": str(uuid4())},
        ):
            with self.subTest(changes=changes):
                VideoTranscodeJob.objects.filter(pk=self.job.pk).update(**{
                    "cancel_requested": False, "state": VideoTranscodeJob.State.RETRY_WAIT,
                    "aws_batch_job_id": failed["jobId"], **changes,
                })
                call_command("reconcile_batch_video_jobs", failed_batch_id=failed["jobId"])
        self.job.refresh_from_db()
        self.assertEqual(self.job.attempt_count, 2)
        submit.assert_not_called()

    @patch("apps.domains.video.management.commands.reconcile_batch_video_jobs._release_reconcile_lock")
    @patch("apps.domains.video.management.commands.reconcile_batch_video_jobs._acquire_reconcile_lock", return_value=True)
    @patch("apps.domains.video.management.commands.reconcile_batch_video_jobs.terminate_batch_job")
    @patch("apps.domains.video.management.commands.reconcile_batch_video_jobs.submit_batch_job")
    @patch("apps.domains.video.management.commands.reconcile_batch_video_jobs._describe_jobs_boto3")
    def test_targeted_recovery_does_not_adopt_replacement_after_cancel(
        self, describe, submit, terminate, _acquire, _release,
    ):
        failed = self._targeted_host_failure()
        replacement = str(uuid4())
        describe.return_value = [failed]

        def submit_after_cancellation(*_args, **_kwargs):
            VideoTranscodeJob.objects.filter(pk=self.job.id).update(state=VideoTranscodeJob.State.CANCELLED)
            return replacement, None

        submit.side_effect = submit_after_cancellation
        call_command("reconcile_batch_video_jobs", failed_batch_id=failed["jobId"])

        self.job.refresh_from_db()
        self.assertEqual(self.job.state, VideoTranscodeJob.State.CANCELLED)
        self.assertEqual(self.job.aws_batch_job_id, failed["jobId"])
        self.assertEqual(terminate.call_args.kwargs["aws_batch_job_id"], replacement)

    @patch("apps.domains.video.management.commands.reconcile_batch_video_jobs._release_reconcile_lock")
    @patch("apps.domains.video.management.commands.reconcile_batch_video_jobs._acquire_reconcile_lock", return_value=True)
    @patch("apps.domains.video.management.commands.reconcile_batch_video_jobs.terminate_batch_job")
    @patch("apps.domains.video.management.commands.reconcile_batch_video_jobs.submit_batch_job")
    @patch("apps.domains.video.management.commands.reconcile_batch_video_jobs._describe_jobs_boto3")
    def test_targeted_recovery_keeps_replacement_that_has_started(
        self, describe, submit, terminate, _acquire, _release,
    ):
        failed = self._targeted_host_failure()
        replacement = str(uuid4())
        describe.return_value = [failed]

        def submit_and_start_job(job_id, **_kwargs):
            self.assertTrue(job_set_running(job_id))
            return replacement, None

        submit.side_effect = submit_and_start_job
        call_command("reconcile_batch_video_jobs", failed_batch_id=failed["jobId"])

        self.job.refresh_from_db()
        self.assertEqual(self.job.state, VideoTranscodeJob.State.RUNNING)
        self.assertEqual(self.job.aws_batch_job_id, replacement)
        terminate.assert_not_called()

    @patch("apps.domains.video.management.commands.reconcile_batch_video_jobs._describe_jobs_boto3")
    @patch("apps.domains.video.management.commands.reconcile_batch_video_jobs.redis_setnx_with_ttl", return_value=None)
    def test_targeted_recovery_requires_live_lock_and_the_exact_argument(self, _redis, describe):
        failed = self._targeted_host_failure()
        with self.assertRaises(CommandError):
            call_command("reconcile_batch_video_jobs", failed_batch_id="not-a-batch-id")
        call_command("reconcile_batch_video_jobs", failed_batch_id=failed["jobId"])
        describe.assert_not_called()

    @patch("apps.domains.video.management.commands.reconcile_batch_video_jobs._acquire_reconcile_lock")
    @patch("apps.domains.video.management.commands.reconcile_batch_video_jobs.submit_batch_job")
    @patch("apps.domains.video.management.commands.reconcile_batch_video_jobs._describe_jobs_boto3")
    def test_targeted_dry_run_reads_current_job_and_preserves_state(self, describe, submit, acquire):
        failed = self._targeted_host_failure()
        describe.return_value = [failed]
        output = StringIO()

        call_command("reconcile_batch_video_jobs", failed_batch_id=failed["jobId"], dry_run=True, stdout=output)

        self.job.refresh_from_db()
        self.assertIn("current recoverable video job", output.getvalue())
        self.assertEqual(self.job.attempt_count, 2)
        self.assertEqual(self.job.aws_batch_job_id, failed["jobId"])
        submit.assert_not_called()
        acquire.assert_not_called()


@override_settings(VIDEO_RETRY_STALE_QUEUED_HOURS=1)
class VideoDetailCanRetryTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(
            name="Video Retry Tenant",
            code="video-retry",
            is_active=True,
        )
        self.video = Video.objects.create(
            tenant=self.tenant,
            title="Retry Video",
            status=Video.Status.PROCESSING,
        )

    def _attach_job(self, *, state, updated_at, aws_batch_job_id="aws-current"):
        job = VideoTranscodeJob.objects.create(
            tenant=self.tenant,
            video=self.video,
            state=state,
            attempt_count=1,
            aws_batch_job_id=aws_batch_job_id,
        )
        VideoTranscodeJob.objects.filter(pk=job.pk).update(updated_at=updated_at)
        self.video.current_job = job
        self.video.save(update_fields=["current_job"])
        self.video.refresh_from_db()
        return job

    def test_can_retry_false_for_recent_queued_job(self):
        self._attach_job(
            state=VideoTranscodeJob.State.QUEUED,
            updated_at=timezone.now() - timedelta(minutes=30),
        )

        self.assertFalse(VideoDetailSerializer(self.video).data["can_retry"])

    def test_can_retry_true_for_stale_queued_job(self):
        self._attach_job(
            state=VideoTranscodeJob.State.QUEUED,
            updated_at=timezone.now() - timedelta(hours=2),
        )

        self.assertTrue(VideoDetailSerializer(self.video).data["can_retry"])

    def test_can_retry_true_for_queued_job_without_batch_id(self):
        self._attach_job(
            state=VideoTranscodeJob.State.RETRY_WAIT,
            updated_at=timezone.now(),
            aws_batch_job_id="",
        )

        self.assertTrue(VideoDetailSerializer(self.video).data["can_retry"])


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

    @patch("apps.domains.video.redis_status_cache.cache_video_status")
    @patch(
        "apps.domains.video.views.video_views.create_job_and_submit_batch",
        return_value=SimpleNamespace(job=None, reject_reason=REASON_SUBMIT_FAILED),
    )
    @patch(
        "apps.domains.video.views.video_views._validate_source_media_via_ffprobe",
        return_value=(True, {"duration": 60}, ""),
    )
    @patch("apps.domains.video.views.video_views.create_presigned_get_url", return_value="https://example.test/raw.mp4")
    @patch("apps.domains.video.views.video_views.head_object", return_value=(True, 1024))
    def test_upload_complete_batch_submit_failure_is_not_reported_as_success(
        self,
        mock_head_object,
        mock_presign,
        mock_probe,
        mock_enqueue,
        mock_cache_status,
    ):
        response = VideoViewSet()._upload_complete_impl(self.video)

        self.assertEqual(response.status_code, 503, response.data)
        self.video.refresh_from_db()
        self.assertEqual(self.video.status, Video.Status.FAILED)
        self.assertEqual(self.video.error_reason, REASON_SUBMIT_FAILED)
        mock_cache_status.assert_any_call(
            tenant_id=self.tenant.id,
            video_id=self.video.id,
            status=Video.Status.UPLOADED,
            duration=60,
            ttl=21600,
        )
        mock_cache_status.assert_any_call(
            tenant_id=self.tenant.id,
            video_id=self.video.id,
            status=Video.Status.FAILED,
            error_reason=REASON_SUBMIT_FAILED,
            ttl=None,
        )
        mock_head_object.assert_called_once()
        mock_presign.assert_called_once()
        mock_probe.assert_called_once()
        mock_enqueue.assert_called_once()

    @patch("apps.domains.video.redis_status_cache.cache_video_status")
    @patch("apps.domains.video.views.video_views.head_object", return_value=(False, 0))
    def test_upload_complete_missing_source_marks_failed(self, mock_head_object, mock_cache_status):
        response = VideoViewSet()._upload_complete_impl(self.video)

        self.assertEqual(response.status_code, 409, response.data)
        self.video.refresh_from_db()
        self.assertEqual(self.video.status, Video.Status.FAILED)
        self.assertEqual(self.video.error_reason, "source_not_found_or_empty")
        mock_cache_status.assert_called_once_with(
            tenant_id=self.tenant.id,
            video_id=self.video.id,
            status=Video.Status.FAILED,
            error_reason="source_not_found_or_empty",
            ttl=None,
        )
        mock_head_object.assert_called_once()
