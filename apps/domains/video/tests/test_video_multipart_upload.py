from __future__ import annotations

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.response import Response
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.models import Tenant, TenantMembership
from apps.domains.video.models import Video
from apps.domains.video.views.video_views import VideoViewSet


User = get_user_model()


class VideoMultipartUploadTests(TestCase):
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
    @patch("apps.domains.video.views.video_views.complete_multipart_upload")
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
