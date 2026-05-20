from __future__ import annotations

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.models import Tenant
from apps.core.models.tenant_membership import TenantMembership
from apps.domains.lectures.models import Lecture, Session
from apps.domains.video.models import Video
from apps.domains.video.serializers import VideoSerializer
from apps.domains.video.views.progress_views import VideoProgressView


User = get_user_model()


class VideoProgressEndpointTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.tenant = Tenant.objects.create(
            name="Video Progress Tenant",
            code="video_progress",
            is_active=True,
        )
        self.user = User.objects.create_user(
            username="video_progress_admin",
            password="test1234",
            tenant=self.tenant,
            is_staff=True,
        )
        TenantMembership.ensure_active(tenant=self.tenant, user=self.user, role="owner")

    def _request(self, video_id: int = 123):
        request = self.factory.get(f"/api/v1/video/videos/{video_id}/progress/")
        request.tenant = self.tenant
        force_authenticate(request, user=self.user)
        return VideoProgressView.as_view()(request, pk=video_id)

    @patch("apps.domains.video.views.progress_views.emit_progress_layer_metrics")
    @patch("apps.domains.video.views.progress_views.get_video_status_from_redis", return_value=None)
    def test_redis_miss_returns_unknown_without_db_query(self, mock_redis, mock_metrics):
        with CaptureQueriesContext(connection) as captured:
            response = self._request(video_id=321)

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["status"], "UNKNOWN")
        self.assertEqual(response.data["state"], "UNKNOWN")
        self.assertEqual(response["Retry-After"], "3")
        mock_redis.assert_called_once_with(self.tenant.id, 321)
        mock_metrics.assert_called_once_with(progress_requests=1, redis_miss=1, db_hit=0)
        self.assertEqual(len(captured), 0, [query["sql"] for query in captured.captured_queries])

    @patch("apps.domains.video.views.progress_views.emit_progress_layer_metrics")
    @patch(
        "apps.domains.video.views.progress_views.get_video_status_from_redis",
        return_value={"status": "READY", "hls_path": "videos/321/master.m3u8", "duration": 73},
    )
    def test_ready_status_uses_redis_payload_without_db_query(self, mock_redis, mock_metrics):
        with CaptureQueriesContext(connection) as captured:
            response = self._request(video_id=321)

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["status"], "READY")
        self.assertEqual(response.data["hls_path"], "videos/321/master.m3u8")
        self.assertEqual(response.data["duration"], 73)
        mock_redis.assert_called_once_with(self.tenant.id, 321)
        mock_metrics.assert_called_once_with(progress_requests=1, redis_miss=0, db_hit=0)
        self.assertEqual(len(captured), 0, [query["sql"] for query in captured.captured_queries])

    @patch("apps.domains.video.views.progress_views.emit_progress_layer_metrics")
    @patch("apps.domains.video.encoding_progress._get_progress_payload")
    @patch(
        "apps.domains.video.views.progress_views.get_video_status_from_redis",
        return_value={"status": "PROCESSING"},
    )
    def test_processing_status_reads_encoding_payload_once(self, mock_redis, mock_payload, mock_metrics):
        mock_payload.return_value = {
            "percent": 31,
            "remaining_seconds": 90,
            "step_index": 2,
            "step_total": 5,
            "step_name_display": "변환 중",
            "step_percent": 40,
        }

        with CaptureQueriesContext(connection) as captured:
            response = self._request(video_id=321)

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["encoding_progress"], 31)
        self.assertEqual(response.data["encoding_remaining_seconds"], 90)
        self.assertEqual(response.data["encoding_step_index"], 2)
        self.assertEqual(response.data["encoding_step_total"], 5)
        self.assertEqual(response.data["encoding_step_name"], "변환 중")
        self.assertEqual(response.data["encoding_step_percent"], 40)
        mock_redis.assert_called_once_with(self.tenant.id, 321)
        mock_payload.assert_called_once_with(321, tenant_id=self.tenant.id)
        mock_metrics.assert_called_once_with(progress_requests=1, redis_miss=0, db_hit=0)
        self.assertEqual(len(captured), 0, [query["sql"] for query in captured.captured_queries])

    @patch("apps.domains.video.views.progress_views.get_video_status_from_redis")
    def test_non_member_cannot_read_tenant_progress_payload(self, mock_redis):
        other_tenant = Tenant.objects.create(
            name="Video Progress Other",
            code="video_progress_other",
            is_active=True,
        )
        non_member = User.objects.create_user(
            username="video_progress_non_member",
            password="test1234",
            tenant=other_tenant,
        )
        request = self.factory.get("/api/v1/video/videos/321/progress/")
        request.tenant = self.tenant
        force_authenticate(request, user=non_member)

        response = VideoProgressView.as_view()(request, pk=321)

        self.assertEqual(response.status_code, 403)
        mock_redis.assert_not_called()


class VideoSerializerEncodingProgressTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(
            name="Video Serializer Tenant",
            code="video_serializer",
            is_active=True,
        )
        lecture = Lecture.objects.create(
            tenant=self.tenant,
            title="Serializer Lecture",
            name="Serializer Lecture",
            subject="math",
        )
        self.session = Session.objects.create(
            lecture=lecture,
            order=1,
            title="1차시",
        )

    @patch("apps.domains.video.encoding_progress._get_progress_payload")
    def test_processing_video_reads_encoding_payload_once(self, mock_payload):
        mock_payload.return_value = {
            "percent": 42,
            "remaining_seconds": 120,
            "step_index": 3,
            "step_total": 7,
            "step_name": "transcoding",
            "step_name_display": "변환 중",
            "step_percent": 55,
        }
        video = Video.objects.create(
            tenant=self.tenant,
            session=self.session,
            title="Processing",
            status=Video.Status.PROCESSING,
        )

        data = VideoSerializer(video).data

        self.assertEqual(data["encoding_progress"], 42)
        self.assertEqual(data["encoding_remaining_seconds"], 120)
        self.assertEqual(data["encoding_step_index"], 3)
        self.assertEqual(data["encoding_step_total"], 7)
        self.assertEqual(data["encoding_step_name"], "변환 중")
        self.assertEqual(data["encoding_step_percent"], 55)
        mock_payload.assert_called_once_with(video.id, tenant_id=self.tenant.id)
