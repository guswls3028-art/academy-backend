"""
Video 삭제 시 AWS Batch Terminate 호출 검증.

- boto3 / batch_control.terminate_batch_job 모킹.
- RUNNING/QUEUED/RETRY_WAIT + aws_batch_job_id 있으면 terminate 호출.
- SUCCEEDED 이면 terminate 미호출.
"""
from unittest.mock import patch, MagicMock

from django.test import TestCase

from apps.support.video.models import Video, VideoTranscodeJob
from apps.support.video.views.video_views import VideoViewSet
from apps.domains.lectures.models import Lecture, Session
from apps.core.models import Tenant


class VideoDeleteTerminateTest(TestCase):
    """Video perform_destroy 시 Batch terminate 호출 여부."""

    def setUp(self):
        tenant = Tenant.objects.create(name="Test Tenant", code="TEST_VIDEO_DEL")
        lecture = Lecture.objects.create(title="L", name="L", tenant=tenant)
        session = Session.objects.create(lecture=lecture, title="S", order=1)
        self.video = Video.objects.create(
            session=session,
            title="V",
            file_key="",
            order=1,
            status=Video.Status.PROCESSING,
        )

    def test_delete_calls_terminate_when_job_running_with_aws_id(self):
        job = VideoTranscodeJob.objects.create(
            video=self.video,
            tenant_id=self.video.session.lecture.tenant_id,
            state=VideoTranscodeJob.State.RUNNING,
            aws_batch_job_id="aws-job-123",
        )
        self.video.current_job_id = job.id
        self.video.save(update_fields=["current_job_id"])

        with patch("apps.support.video.services.batch_control.terminate_batch_job") as mock_terminate:
            with patch("apps.support.video.views.video_views.enqueue_delete_r2"):
                view = VideoViewSet()
                view.perform_destroy(self.video)

        mock_terminate.assert_called_once()
        call_kw = mock_terminate.call_args[1]
        self.assertEqual(call_kw["video_id"], self.video.id)
        self.assertEqual(call_kw["job_id"], str(job.id))
        self.assertEqual(mock_terminate.call_args[0][0], "aws-job-123")
        self.assertEqual(mock_terminate.call_args[0][1], "video_deleted")

    def test_delete_does_not_call_terminate_when_job_succeeded(self):
        job = VideoTranscodeJob.objects.create(
            video=self.video,
            tenant_id=self.video.session.lecture.tenant_id,
            state=VideoTranscodeJob.State.SUCCEEDED,
            aws_batch_job_id="aws-job-456",
        )
        self.video.current_job_id = job.id
        self.video.save(update_fields=["current_job_id"])

        with patch("apps.support.video.services.batch_control.terminate_batch_job") as mock_terminate:
            with patch("apps.support.video.views.video_views.enqueue_delete_r2"):
                view = VideoViewSet()
                view.perform_destroy(self.video)

        mock_terminate.assert_not_called()

    def test_delete_calls_terminate_for_queued_job_with_aws_id(self):
        job = VideoTranscodeJob.objects.create(
            video=self.video,
            tenant_id=self.video.session.lecture.tenant_id,
            state=VideoTranscodeJob.State.QUEUED,
            aws_batch_job_id="aws-queued-789",
        )
        self.video.current_job_id = job.id
        self.video.save(update_fields=["current_job_id"])

        with patch("apps.support.video.services.batch_control.terminate_batch_job") as mock_terminate:
            with patch("apps.support.video.views.video_views.enqueue_delete_r2"):
                view = VideoViewSet()
                view.perform_destroy(self.video)

        mock_terminate.assert_called_once()
        self.assertEqual(mock_terminate.call_args[0][0], "aws-queued-789")

    def test_delete_terminate_failure_does_not_raise(self):
        job = VideoTranscodeJob.objects.create(
            video=self.video,
            tenant_id=self.video.session.lecture.tenant_id,
            state=VideoTranscodeJob.State.RUNNING,
            aws_batch_job_id="aws-fail-me",
        )
        self.video.current_job_id = job.id
        self.video.save(update_fields=["current_job_id"])

        with patch("apps.support.video.services.batch_control.terminate_batch_job") as mock_terminate:
            mock_terminate.side_effect = Exception("network error")
            with patch("apps.support.video.views.video_views.enqueue_delete_r2"):
                view = VideoViewSet()
                view.perform_destroy(self.video)

        self.assertFalse(Video.objects.filter(pk=self.video.id).exists())
