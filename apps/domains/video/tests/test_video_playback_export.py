from datetime import timedelta
from unittest.mock import patch
from uuid import uuid4

from django.apps import apps
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.models import OpsAuditLog, Tenant, TenantMembership
from apps.core.r2_paths import video_hls_master_path, video_playback_export_key
from apps.domains.video.models import Video, VideoPlaybackExport
from apps.domains.video.views.video_views import VideoViewSet
from apps.worker.video_worker.playback_export_main import process_export


SERVICE = "apps.domains.video.services.playback_export."


class VideoPlaybackExportTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(name="Export Tenant", code="video-export-test")
        cls.other = Tenant.objects.create(name="Other Tenant", code="video-export-other")
        User = get_user_model()
        cls.teacher = User.objects.create_user(username="export-teacher", tenant=cls.tenant)
        cls.student = User.objects.create_user(username="export-student", tenant=cls.tenant)
        cls.other_teacher = User.objects.create_user(username="export-other", tenant=cls.other)
        TenantMembership.ensure_active(tenant=cls.tenant, user=cls.teacher, role="teacher")
        TenantMembership.ensure_active(tenant=cls.tenant, user=cls.student, role="student")
        TenantMembership.ensure_active(tenant=cls.other, user=cls.other_teacher, role="teacher")
        lecture = apps.get_model("lectures", "Lecture").objects.create(
            tenant=cls.tenant, title="강의", name="강의", subject="MATH",
        )
        session = apps.get_model("lectures", "Session").objects.create(lecture=lecture, title="1차시", order=1)
        cls.video, cls.second = [
            Video.objects.create(
                tenant=cls.tenant, session=session, title=title, order=order,
                status=Video.Status.READY, source_type=Video.SourceType.UPLOADED,
            )
            for order, title in enumerate(["지난 차시", "다른 차시"], start=1)
        ]
        for video in (cls.video, cls.second):
            video.hls_path = video_hls_master_path(cls.tenant.pk, video.pk)
            video.save(update_fields=["hls_path"])

    def _request(self, method="post", *, video=None, user=None, tenant=None):
        factory = APIRequestFactory()
        request = getattr(factory, method)("/api/v1/media/videos/playback-export/")
        request.tenant = tenant or self.tenant
        force_authenticate(request, user=user or self.teacher)
        return VideoViewSet.as_view({method: "playback_export"})(request, pk=(video or self.video).pk)

    @patch(SERVICE + "_batch_target", return_value=("video-queue", "video-worker", "ap-northeast-2"))
    @patch(SERVICE + "submit_video_playback_export", return_value="batch-job")
    def test_one_export_is_shared_across_requests_and_queue_is_bounded_per_tenant(self, submit, _target):
        self.assertEqual(self._request("get").data, {"state": "not_requested"})
        self.assertEqual(self._request().status_code, 202)
        self.assertEqual(self._request().status_code, 202)
        self.assertEqual(self._request("get").data, {"state": "preparing"})
        self.assertEqual(self._request(video=self.second).status_code, 429)
        export = VideoPlaybackExport.objects.get(video=self.video)
        submit.assert_called_once_with(
            export_id=export.pk, attempt=str(export.attempt),
            queue_name="video-queue", job_definition="video-worker", region="ap-northeast-2",
        )
        self.assertEqual(OpsAuditLog.objects.filter(action="video.playback_export_requested").count(), 1)

    @patch(SERVICE + "submit_video_playback_export")
    @patch(SERVICE + "head_video_source_download")
    def test_student_other_tenant_noncanonical_and_development_cannot_start_work(self, head, submit):
        self.assertEqual(self._request(user=self.student).status_code, 403)
        self.assertEqual(self._request(tenant=self.other, user=self.other_teacher).status_code, 404)
        self.assertEqual(self._request().status_code, 503)
        Video.objects.filter(pk=self.video.pk).update(hls_path="tenants/999/video/hls/1/master.m3u8")
        self.assertEqual(self._request().status_code, 400)
        self.assertFalse(VideoPlaybackExport.objects.exists())
        head.assert_not_called()
        submit.assert_not_called()

    @patch(SERVICE + "create_video_source_download_url", return_value="https://download.example.test/file")
    @patch(SERVICE + "head_video_source_download", return_value=True)
    def test_only_explicit_staff_download_signs_ready_asset_without_caching(self, head, sign):
        export = VideoPlaybackExport.objects.create(
            video=self.video, status=VideoPlaybackExport.Status.READY,
            expires_at=timezone.now() + timedelta(hours=4),
        )
        self.assertEqual(self._request("get").data, {"state": "ready"})
        sign.assert_not_called()
        response = self._request()
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response["Cache-Control"], "private, no-store")
        self.assertEqual(response.data["filename"], "지난 차시.mp4")
        key = video_playback_export_key(self.tenant.pk, self.video.pk, export.attempt)
        head.assert_called_once_with(key=key)
        sign.assert_called_once_with(key=key, filename="지난 차시.mp4", expires_in=300)
        audit = OpsAuditLog.objects.get(action="video.playback_export_download")
        self.assertEqual(audit.payload, {"video_id": self.video.pk, "source_video_id": self.video.pk})

    @patch(SERVICE + "_batch_target", return_value=("video-queue", "video-worker", "ap-northeast-2"))
    @patch(SERVICE + "submit_video_playback_export", side_effect=RuntimeError)
    def test_queue_failure_is_retryable_and_does_not_leave_a_live_lease(self, submit, _target):
        self.assertEqual(self._request().status_code, 503)
        export = VideoPlaybackExport.objects.get(video=self.video)
        self.assertEqual(export.status, VideoPlaybackExport.Status.FAILED)
        self.assertEqual(self._request("get").data, {"state": "failed"})
        self.assertEqual(self._request().data, {"state": "failed"})
        self.assertEqual(submit.call_count, 1)

    @patch(SERVICE + "_batch_target", return_value=("video-queue", "video-worker", "ap-northeast-2"))
    @patch(SERVICE + "submit_video_playback_export", return_value="batch-job")
    def test_reused_lesson_uses_the_same_export_even_after_origin_unlink(self, submit, _target):
        destination = apps.get_model("lectures", "Session").objects.create(
            lecture=self.video.session.lecture, title="2차시", order=2,
        )
        alias = Video.objects.create(
            tenant=self.tenant, session=destination, title="복습", status=Video.Status.READY,
            source_type=Video.SourceType.UPLOADED, reused_from=self.video, hls_path=self.video.hls_path,
        )
        self.assertEqual(self._request().status_code, 202)
        self.video.delete()
        self.assertEqual(self._request("get").status_code, 404)
        self.assertEqual(self._request("get", video=alias).data, {"state": "preparing"})
        self.assertEqual(self._request(video=alias).status_code, 202)
        self.assertEqual(VideoPlaybackExport.objects.get().video_id, self.video.pk)
        self.assertEqual(submit.call_count, 1)

    @patch.dict("os.environ", {"AWS_BATCH_JOB_ID": "synthetic-worker-id"})
    @patch("academy.adapters.video.playback_export.build_playback_mp4")
    @patch("academy.adapters.storage.r2_objects.video_bucket_client")
    def test_worker_claims_only_current_attempt_and_makes_the_mp4_ready_once(self, storage, render):
        destination = apps.get_model("lectures", "Session").objects.create(
            lecture=self.video.session.lecture, title="2차시", order=2,
        )
        alias = Video.objects.create(
            tenant=self.tenant, session=destination, title="복습", status=Video.Status.READY,
            source_type=Video.SourceType.UPLOADED, reused_from=self.video, hls_path=self.video.hls_path,
        )
        export = VideoPlaybackExport.objects.create(video=self.video)
        self.video.delete()
        uploaded = {}

        def prepare(*, bucket, master_key, output):
            self.assertEqual(master_key, self.video.hls_path)
            output.write_bytes(b"synthetic-mp4")

        def upload(file, bucket, key, *, ExtraArgs):
            uploaded[key] = file.read()
            self.assertEqual(ExtraArgs, {"ContentType": "video/mp4", "CacheControl": "private, no-store"})

        render.side_effect = prepare
        storage.return_value.upload_fileobj.side_effect = upload
        self.assertFalse(process_export(export.pk, uuid4()))
        self.assertTrue(process_export(export.pk, export.attempt))
        self.assertFalse(process_export(export.pk, export.attempt))
        self.assertEqual(self._request("get", video=alias).data, {"state": "ready"})
        self.assertEqual(uploaded, {
            video_playback_export_key(self.tenant.pk, self.video.pk, export.attempt): b"synthetic-mp4",
        })
        self.assertEqual(render.call_count, 1)
