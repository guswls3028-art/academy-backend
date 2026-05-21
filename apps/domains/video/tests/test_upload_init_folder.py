from __future__ import annotations

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.models import Tenant, TenantMembership
from apps.domains.lectures.models import Lecture, Session
from apps.domains.video.models import Video, VideoFolder
from apps.domains.video.views.video_views import VideoViewSet


User = get_user_model()


class VideoUploadInitFolderTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.tenant = Tenant.objects.create(
            name="Video Folder Tenant",
            code="video-folder",
            is_active=True,
        )
        self.user = User.objects.create_user(
            username="video_folder_admin",
            password="test1234",
            tenant=self.tenant,
            is_staff=True,
        )
        TenantMembership.ensure_active(tenant=self.tenant, user=self.user, role="admin")

    def _post_upload_init(self, payload: dict):
        request = self.factory.post("/api/v1/media/videos/upload/init/", payload, format="json")
        request.tenant = self.tenant
        force_authenticate(request, user=self.user)
        view = VideoViewSet.as_view({"post": "upload_init"})
        with patch(
            "apps.domains.video.views.video_views.create_presigned_put_url",
            return_value="https://uploads.example/video.mp4",
        ):
            return view(request)

    def test_public_upload_init_saves_selected_folder(self):
        lecture = Lecture.get_or_create_system_lecture(self.tenant)
        session = Session.objects.create(lecture=lecture, title="전체공개영상", order=1)
        folder = VideoFolder.objects.create(
            tenant=self.tenant,
            session=session,
            name="하위 폴더",
        )

        response = self._post_upload_init(
            {
                "session": session.id,
                "folder": folder.id,
                "title": "폴더 영상",
                "filename": "lesson.mp4",
                "content_type": "video/mp4",
            }
        )

        self.assertEqual(response.status_code, 201, response.data)
        video = Video.objects.get(pk=response.data["video"]["id"])
        self.assertEqual(video.folder_id, folder.id)
        self.assertEqual(response.data["video"]["folder"], folder.id)

    def test_upload_init_rejects_folder_for_non_public_session(self):
        lecture = Lecture.objects.create(
            tenant=self.tenant,
            title="일반 강의",
            name="일반 강의",
            subject="MATH",
        )
        session = Session.objects.create(lecture=lecture, title="1차시", order=1)
        folder = VideoFolder.objects.create(tenant=self.tenant, name="공개 폴더")

        response = self._post_upload_init(
            {
                "session": session.id,
                "folder": folder.id,
                "title": "잘못된 폴더 영상",
                "filename": "lesson.mp4",
            }
        )

        self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(Video.objects.count(), 0)

    def test_upload_init_rejects_other_tenant_folder(self):
        other_tenant = Tenant.objects.create(
            name="Other Tenant",
            code="other-video-folder",
            is_active=True,
        )
        lecture = Lecture.get_or_create_system_lecture(self.tenant)
        session = Session.objects.create(lecture=lecture, title="전체공개영상", order=1)
        folder = VideoFolder.objects.create(tenant=other_tenant, name="다른 테넌트 폴더")

        response = self._post_upload_init(
            {
                "session": session.id,
                "folder": folder.id,
                "title": "크로스 테넌트 영상",
                "filename": "lesson.mp4",
            }
        )

        self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(Video.objects.count(), 0)
