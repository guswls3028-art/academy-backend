from __future__ import annotations

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db.utils import IntegrityError
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.models import Tenant, TenantMembership
from apps.domains.video.views.video_views import VideoViewSet


User = get_user_model()


class VideoFolderRaceGuardTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.tenant = Tenant.objects.create(
            name="Video Folder Race",
            code="video-folder-race",
            is_active=True,
        )
        self.user = User.objects.create_user(
            username="video-folder-race-admin",
            password="test1234",
            tenant=self.tenant,
            is_staff=True,
        )
        TenantMembership.ensure_active(tenant=self.tenant, user=self.user, role="admin")

    def _post_folder(self, payload: dict):
        request = self.factory.post("/api/v1/media/videos/folders/", payload, format="json")
        request.tenant = self.tenant
        force_authenticate(request, user=self.user)
        return VideoViewSet.as_view({"post": "folders"})(request)

    def test_folder_create_returns_conflict_on_concurrent_duplicate_integrity_error(self):
        with patch(
            "apps.domains.video.views.video_views.VideoFolder.objects.create",
            side_effect=IntegrityError("duplicate folder"),
        ):
            response = self._post_folder({"name": "동시 생성 폴더"})

        self.assertEqual(response.status_code, 409, response.data)
        self.assertEqual(response.data["detail"], "Folder with this name already exists")
