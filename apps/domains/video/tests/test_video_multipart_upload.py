from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from botocore.exceptions import ClientError

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, override_settings
from rest_framework.response import Response
from rest_framework.test import APIRequestFactory, force_authenticate

from academy.adapters.storage.r2_objects import completed_multipart_object_size
from academy.adapters.storage.r2_presign import MultipartCompletionMismatch, complete_multipart_upload
from apps.core.models import Tenant, TenantMembership
from apps.domains.video.models import Video
from apps.domains.video.views.video_views import VideoViewSet


User = get_user_model()


@override_settings(R2_VIDEO_BUCKET="test-video-bucket")
class MultipartStorageReadbackTests(SimpleTestCase):
    @patch("academy.adapters.storage.r2_presign.completed_multipart_object_size", return_value=110_000_000)
    @patch("academy.adapters.storage.r2_presign._presign")
    def test_no_such_upload_requires_matching_stored_parts(self, provider, size):
        provider.return_value.complete_multipart_upload.side_effect = ClientError(
            {"Error": {"Code": "NoSuchUpload"}}, "CompleteMultipartUpload",
        )
        parts = [{"ETag": '"bce6bf66aeb76c7040fdd5f4eccb78e6"', "PartNumber": 1}]
        self.assertEqual(complete_multipart_upload("video.mp4", "upload-id", parts), 110_000_000)
        size.assert_called_once_with(key="video.mp4", parts=parts)
        size.return_value = None
        with self.assertRaises(MultipartCompletionMismatch):
            complete_multipart_upload("video.mp4", "upload-id", parts)

    @patch("academy.adapters.storage.r2_objects.video_bucket_client")
    def test_exact_key_and_ordered_part_etag_required(self, make_client):
        head = make_client.return_value.head_object
        head.return_value = {"ETag": '"f77dc0eecdebcd774a2a22cb393ad2ff-2"', "ContentLength": 110_000_000}
        parts = [
            {"ETag": '"bce6bf66aeb76c7040fdd5f4eccb78e6"', "PartNumber": 1},
            {"ETag": '"8165449fc15bbf43d3b674595cbcc406"', "PartNumber": 2},
        ]
        key = "tenants/10/video/raw/test.mp4"

        self.assertEqual(completed_multipart_object_size(key=key, parts=parts), 110_000_000)
        head.assert_called_once_with(Bucket="test-video-bucket", Key=key)
        self.assertIsNone(completed_multipart_object_size(key=key, parts=parts[::-1]))
        head.side_effect = ClientError({"Error": {"Code": "NoSuchKey"}}, "HeadObject")
        self.assertIsNone(completed_multipart_object_size(key=key, parts=parts))


class VideoMultipartUploadTests(TestCase):
    multipart_payload = {
        "upload_id": "upload-101mb",
        "parts": [{"ETag": '"bce6bf66aeb76c7040fdd5f4eccb78e6"', "PartNumber": 1}],
    }

    def setUp(self):
        self.factory = APIRequestFactory()
        self.tenant = Tenant.objects.create(
            name="Multipart Tenant",
            code="multipart-tenant",
            is_active=True,
        )
        self.user = User.objects.create_user(
            username="multipart_admin",
            password="test1234",
            tenant=self.tenant,
            is_staff=True,
        )
        TenantMembership.ensure_active(tenant=self.tenant, user=self.user, role="admin")
        self.video = Video.objects.create(
            tenant=self.tenant,
            title="101MB 영상",
            file_key="tenant/multipart/video.mp4",
            status=Video.Status.PENDING,
        )

    def _post(self, action: str, payload: dict, *, video_id: int | None = None):
        request = self.factory.post("/api/v1/media/videos/multipart/", payload, format="json")
        request.tenant = self.tenant
        force_authenticate(request, user=self.user)
        view = VideoViewSet.as_view({"post": action})
        return view(request, pk=video_id or self.video.id)

    @patch.object(
        VideoViewSet,
        "_trigger_upload_complete_pipeline",
        return_value=Response({"ok": True}),
    )
    @patch("apps.domains.video.views.video_views.complete_multipart_upload", return_value=None)
    def test_complete_sorts_parts_before_storage(self, complete_mock, _pipeline_mock):
        response = self._post(
            "upload_multipart_complete",
            {
                "upload_id": "upload-101mb",
                "parts": [
                    {"ETag": '"part-2"', "PartNumber": 2},
                    {"ETag": '"part-1"', "PartNumber": 1},
                ],
            },
        )

        self.assertEqual(response.status_code, 200, response.data)
        complete_mock.assert_called_once_with(
            key=self.video.file_key,
            upload_id="upload-101mb",
            parts=[
                {"ETag": '"part-1"', "PartNumber": 1},
                {"ETag": '"part-2"', "PartNumber": 2},
            ],
        )

    @patch("apps.domains.video.views.video_views.create_presigned_upload_part_url")
    def test_presign_rejects_invalid_or_duplicate_part_numbers(self, presign_mock):
        for part_numbers in ([0], [-1], [10001], [1, 1], ["invalid"], [True], {"1": 1}):
            with self.subTest(part_numbers=part_numbers):
                response = self._post(
                    "upload_multipart_presign",
                    {"upload_id": "upload-101mb", "part_numbers": part_numbers},
                )
                self.assertEqual(response.status_code, 400, response.data)

        presign_mock.assert_not_called()

    @patch("apps.domains.video.views.video_views.complete_multipart_upload")
    def test_complete_rejects_invalid_or_duplicate_parts(self, complete_mock):
        for parts in (
            [{"ETag": '"part-1"', "PartNumber": 0}],
            [{"ETag": '"part-1"', "PartNumber": True}],
            [{"ETag": "", "PartNumber": 1}],
            [
                {"ETag": '"part-1"', "PartNumber": 1},
                {"ETag": '"part-1-again"', "PartNumber": 1},
            ],
            {"ETag": '"part-1"', "PartNumber": 1},
        ):
            with self.subTest(parts=parts):
                response = self._post(
                    "upload_multipart_complete",
                    {"upload_id": "upload-101mb", "parts": parts},
                )
                self.assertEqual(response.status_code, 400, response.data)

        complete_mock.assert_not_called()

    @patch("apps.domains.video.views.video_views._cache_uploaded_status")
    @patch("apps.domains.video.views.video_views.create_job_and_submit_batch",
           return_value=SimpleNamespace(job=object(), reject_reason=None))
    @patch("apps.domains.video.views.video_views._validate_source_media_via_ffprobe",
           return_value=(True, {"duration": 60}, ""))
    @patch("apps.domains.video.views.video_views.create_presigned_get_url", return_value="https://example.test/raw.mp4")
    @patch("apps.domains.video.views.video_views.abort_multipart_upload")
    @patch("apps.domains.video.views.video_views.complete_multipart_upload", return_value=110_000_000)
    def test_retry_recovers_assembled_object_and_replay_does_not_enqueue_again(
        self, assemble, abort, _presign, _probe, enqueue, _cache,
    ):
        response = self._post("upload_multipart_complete", self.multipart_payload)
        self.assertEqual(response.status_code, 200, response.data)
        self.video.refresh_from_db()
        self.assertEqual(self.video.status, Video.Status.UPLOADED)

        replay = self._post("upload_multipart_complete", self.multipart_payload)
        self.assertEqual(replay.status_code, 200, replay.data)
        self.assertEqual(replay.data["id"], self.video.id)
        assemble.assert_called_once()
        enqueue.assert_called_once()
        abort.assert_not_called()

    @patch("apps.domains.video.views.video_views.abort_multipart_upload")
    @patch("apps.domains.video.views.video_views.complete_multipart_upload",
           side_effect=OSError("storage unavailable"))
    def test_transient_assembly_failure_keeps_parts_and_pending_state(self, _assemble, abort):
        response = self._post("upload_multipart_complete", self.multipart_payload)
        self.assertEqual(response.status_code, 502, response.data)
        self.video.refresh_from_db()
        self.assertEqual(self.video.status, Video.Status.PENDING)
        abort.assert_not_called()

    @patch("apps.domains.video.views.video_views.complete_multipart_upload",
           side_effect=MultipartCompletionMismatch)
    @patch.object(VideoViewSet, "_trigger_upload_complete_pipeline")
    def test_replay_does_not_process_a_different_stored_object(self, pipeline, _assemble):
        response = self._post("upload_multipart_complete", self.multipart_payload)
        self.assertEqual(response.status_code, 409, response.data)
        self.video.refresh_from_db()
        self.assertEqual(self.video.status, Video.Status.PENDING)
        pipeline.assert_not_called()

    @patch("apps.domains.video.views.video_views.complete_multipart_upload")
    def test_foreign_tenant_cannot_assemble_or_replay(self, assemble):
        foreign = Tenant.objects.create(name="Other upload tenant", code="other-upload-tenant")
        video = Video.objects.create(tenant=foreign, title="Other video", status=Video.Status.READY)
        response = self._post("upload_multipart_complete", self.multipart_payload, video_id=video.id)
        self.assertEqual(response.status_code, 404, response.data)
        assemble.assert_not_called()

    @patch("apps.domains.video.views.video_views.abort_multipart_upload")
    def test_abort_uses_exact_tenant_video_key(self, abort_mock):
        response = self._post(
            "upload_multipart_abort",
            {"upload_id": "upload-101mb"},
        )

        self.assertEqual(response.status_code, 200, response.data)
        abort_mock.assert_called_once_with(
            key=self.video.file_key,
            upload_id="upload-101mb",
        )

    @patch("apps.domains.video.views.video_views.abort_multipart_upload")
    def test_foreign_tenant_video_is_not_mutated(self, abort_mock):
        foreign = Tenant.objects.create(
            name="Foreign Multipart Tenant",
            code="foreign-multipart",
            is_active=True,
        )
        foreign_video = Video.objects.create(
            tenant=foreign,
            title="다른 학원 영상",
            file_key="foreign/video.mp4",
            status=Video.Status.PENDING,
        )

        response = self._post(
            "upload_multipart_abort",
            {"upload_id": "foreign-upload"},
            video_id=foreign_video.id,
        )

        self.assertEqual(response.status_code, 404, response.data)
        abort_mock.assert_not_called()
