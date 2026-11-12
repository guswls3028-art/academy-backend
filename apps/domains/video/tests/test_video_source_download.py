from unittest.mock import patch

from django.apps import apps
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from academy.adapters.storage.r2_objects import VideoSourceStorageError
from apps.core.models import OpsAuditLog, Tenant, TenantMembership
from apps.core.r2_paths import video_raw_key
from apps.domains.video.models import Video
from apps.domains.video.views.video_views import VideoViewSet


class VideoSourceDownloadTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(name="Upload Tenant", code="upload-tenant")
        cls.other = Tenant.objects.create(name="Other Tenant", code="other-upload-tenant")
        User = get_user_model()
        cls.teacher = User.objects.create_user(username="download-teacher", tenant=cls.tenant)
        cls.student = User.objects.create_user(username="download-student", tenant=cls.tenant)
        cls.other_teacher = User.objects.create_user(username="download-other", tenant=cls.other)
        TenantMembership.ensure_active(tenant=cls.tenant, user=cls.teacher, role="teacher")
        TenantMembership.ensure_active(tenant=cls.tenant, user=cls.student, role="student")
        TenantMembership.ensure_active(tenant=cls.other, user=cls.other_teacher, role="teacher")
        lecture = apps.get_model("lectures", "Lecture").objects.create(
            tenant=cls.tenant, title="강의", name="강의", subject="MATH",
        )
        cls.session = apps.get_model("lectures", "Session").objects.create(lecture=lecture, title="1차시", order=1)
        cls.key = video_raw_key(
            tenant_id=cls.tenant.pk, session_id=cls.session.pk,
            unique_id="11111111-1111-4111-8111-111111111111", ext="mov",
        )
        cls.video = Video.objects.create(
            tenant=cls.tenant, session=cls.session, title="물질: 특성",
            source_type=Video.SourceType.UPLOADED, status=Video.Status.READY, file_key=cls.key,
        )

    def _request(self, *, user=None, tenant=None, data=None):
        request = APIRequestFactory().post("/api/v1/media/videos/source-download/", data or {}, format="json")
        request.tenant = tenant or self.tenant
        force_authenticate(request, user=user or self.teacher)
        return VideoViewSet.as_view({"post": "source_download"})(request, pk=self.video.pk)

    @patch("apps.domains.video.services.source_download.create_video_source_download_url",
           return_value="https://download.example.test/file?signature=private")
    @patch("apps.domains.video.services.source_download.head_video_source_download", return_value=True)
    def test_staff_download_uses_only_own_original_and_audits_issuance(self, head, sign):
        response = self._request(data={"file_key": "untrusted"})
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["filename"], "물질  특성.mov")
        self.assertEqual(response.data["expires_in"], 300)
        self.assertEqual(response["Cache-Control"], "private, no-store")
        head.assert_called_once_with(key=self.key)
        sign.assert_called_once_with(key=self.key, filename="물질  특성.mov", expires_in=300)
        audit = OpsAuditLog.objects.get(action="video.source_download")
        self.assertEqual(audit.target_tenant, self.tenant)
        self.assertEqual(audit.actor_user, self.teacher)
        self.assertEqual(audit.payload, {"video_id": self.video.pk})

    @patch("apps.domains.video.services.source_download.head_video_source_download")
    def test_students_other_tenants_and_corrupt_foreign_keys_do_not_touch_storage(self, head):
        self.assertEqual(self._request(user=self.student).status_code, 403)
        self.assertEqual(self._request(user=self.other_teacher, tenant=self.other).status_code, 404)
        Video.objects.filter(pk=self.video.pk).update(
            file_key=self.key.replace(f"tenants/{self.tenant.pk}/", f"tenants/{self.other.pk}/"),
        )
        self.assertEqual(self._request().status_code, 404)
        head.assert_not_called()

    @patch("apps.domains.video.services.source_download.head_video_source_download")
    def test_pending_and_external_originals_do_not_touch_storage(self, head):
        Video.objects.filter(pk=self.video.pk).update(status=Video.Status.PENDING)
        self.assertEqual(self._request().data["code"], "video_source_uploading")
        Video.objects.filter(pk=self.video.pk).update(source_type=Video.SourceType.YOUTUBE, status=Video.Status.READY)
        self.assertEqual(self._request().data["code"], "video_source_external")
        head.assert_not_called()

    def test_metadata_patch_cannot_replace_the_source_object(self):
        request = APIRequestFactory().patch(
            "/api/v1/media/videos/", {"file_key": "untrusted", "title": "제목 수정"}, format="json",
        )
        request.tenant = self.tenant
        force_authenticate(request, user=self.teacher)
        response = VideoViewSet.as_view({"patch": "partial_update"})(request, pk=self.video.pk)
        self.assertEqual(response.status_code, 200, response.data)
        self.video.refresh_from_db()
        self.assertEqual(self.video.file_key, self.key)
        self.assertEqual(self.video.title, "제목 수정")

    @patch("apps.domains.video.services.source_download.create_video_source_download_url")
    @patch("apps.domains.video.services.source_download.head_video_source_download", return_value=False)
    def test_missing_or_unavailable_storage_never_signs(self, head, sign):
        self.assertEqual(self._request().data["code"], "video_source_missing")
        head.side_effect = VideoSourceStorageError
        self.assertEqual(self._request().status_code, 503)
        sign.assert_not_called()

    @patch("apps.domains.video.services.source_download.create_video_source_download_url")
    @patch("apps.domains.video.services.source_download.head_video_source_download")
    def test_deleted_while_checking_storage_never_signs(self, head, sign):
        def delete_during_check(*, key):
            self.assertEqual(key, self.key)
            self.video.delete()
            return True

        head.side_effect = delete_during_check
        self.assertEqual(self._request().status_code, 404)
        sign.assert_not_called()
