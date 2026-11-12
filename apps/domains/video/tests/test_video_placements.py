from datetime import timedelta
from io import StringIO
from unittest.mock import patch
from urllib.parse import urlsplit

from django.core.management import call_command
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITestCase

from apps.core.models import OpsAuditLog, Tenant, TenantMembership, User
from apps.core.r2_paths import video_hls_master_path
from apps.domains.enrollment.test_support import create_enrollment_fixture, create_session_enrollment_fixture
from apps.domains.lectures.test_support import create_lecture_fixture, create_session_fixture
from apps.domains.students.test_support import create_student_fixture
from apps.domains.video.models import Video, VideoProgress


class VideoPlacementsTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(code="video-links", name="Video Links")
        cls.other_tenant = Tenant.objects.create(code="other-video-links", name="Other Video Links")
        cls.teacher = User.objects.create_user(username="links-teacher", tenant=cls.tenant)
        cls.student_user = User.objects.create_user(username="links-student", tenant=cls.tenant)
        cls.other_teacher = User.objects.create_user(username="other-links-teacher", tenant=cls.other_tenant)
        TenantMembership.ensure_active(tenant=cls.tenant, user=cls.teacher, role="teacher")
        TenantMembership.ensure_active(tenant=cls.tenant, user=cls.student_user, role="student")
        TenantMembership.ensure_active(tenant=cls.other_tenant, user=cls.other_teacher, role="teacher")
        old_lecture = create_lecture_fixture(tenant=cls.tenant, title="지난 강의", name="지난 강의", subject="MATH")
        cls.target = create_lecture_fixture(tenant=cls.tenant, title="새 강의", name="새 강의", subject="MATH")
        other_lecture = create_lecture_fixture(
            tenant=cls.other_tenant, title="다른 학원", name="다른 학원", subject="MATH",
        )
        cls.original_session = create_session_fixture(lecture=old_lecture, title="복습 원본", order=1)
        cls.first = create_session_fixture(lecture=cls.target, title="새 강의 1", order=1)
        cls.second = create_session_fixture(lecture=cls.target, title="새 강의 2", order=2)
        cls.foreign = create_session_fixture(lecture=other_lecture, title="다른 학원 1", order=1)
        cls.original = Video.objects.create(
            tenant=cls.tenant, session=cls.original_session, title="기존 강의 영상",
            status=Video.Status.READY, duration=600, file_key="",
        )
        cls.original.hls_path = video_hls_master_path(cls.tenant.pk, cls.original.pk)
        cls.original.save(update_fields=["hls_path"])
        cls.student = create_student_fixture(
            tenant=cls.tenant, user=cls.student_user, name="테스트 학생", ps_number="PLACEMENT-1",
            omr_code="12345678", parent_phone="01000000000",
        )
        cls.enrollment = create_enrollment_fixture(
            tenant=cls.tenant, student=cls.student, lecture=cls.target, status="ACTIVE",
        )
        create_session_enrollment_fixture(tenant=cls.tenant, session=cls.first, enrollment=cls.enrollment)

    def setUp(self):
        self.client.force_authenticate(self.teacher)

    def _path(self, video=None):
        return f"/api/v1/media/videos/{(video or self.original).pk}/placements/"

    def _headers(self, tenant=None):
        return {"HTTP_HOST": "localhost", "HTTP_X_TENANT_CODE": (tenant or self.tenant).code}

    def _attach(self, *sessions, source=None):
        return self.client.patch(
            self._path(source), {"add_session_ids": [session.pk for session in sessions]},
            format="json", **self._headers(),
        )

    def _link(self, session):
        return Video.objects.get(reused_from=self.original, session=session)

    def test_bulk_attach_is_idempotent_and_uses_existing_media_without_raw(self):
        response = self._attach(self.first, self.second, self.original_session)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual({row["session_id"] for row in response.data["placements"]}, {
            self.original_session.pk, self.first.pk, self.second.pk,
        })
        link = self._link(self.first)
        self.assertEqual(link.file_key, "")
        self.assertEqual(link.hls_path, self.original.hls_path)
        self.assertEqual(link.visibility, Video.Visibility.ENROLLED)
        self.assertEqual(link.view_count, 0)
        self.assertEqual(self._attach(self.second, source=link).status_code, 200)
        self.assertEqual(Video.objects.filter(reused_from=self.original).count(), 2)
        self.assertEqual(OpsAuditLog.objects.filter(action="video.attach_placements").count(), 1)

    @override_settings(CDN_HLS_BASE_URL="https://video.example.test")
    def test_only_destination_entitlement_authorizes_playback_of_the_shared_asset(self):
        self.assertEqual(self._attach(self.first, self.second).status_code, 200)
        linked = self._link(self.first)
        other = self._link(self.second)
        self.client.force_authenticate(self.student_user)
        for video in (self.original, other):
            response = self.client.get(
                f"/api/v1/student/video/videos/{video.pk}/playback/?access_check=true", **self._headers(),
            )
            self.assertEqual(response.status_code, 403, response.data)
        with patch("apps.domains.video.services.playback_session.init_session_redis"):
            response = self.client.post(
                f"/api/v1/student/video/videos/{linked.pk}/playback/",
                {"device_id": "video-placement-qa"}, format="json", **self._headers(),
            )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(urlsplit(response.data["hls_url"]).path, "/" + self.original.hls_path)

    def test_cross_tenant_target_and_non_staff_requests_are_rejected_atomically(self):
        response = self._attach(self.first, self.foreign)
        self.assertEqual(response.status_code, 404, response.data)
        self.assertFalse(Video.objects.filter(reused_from=self.original).exists())
        self.client.force_authenticate(self.other_teacher)
        self.assertEqual(self.client.get(self._path(), **self._headers(self.other_tenant)).status_code, 404)
        self.client.force_authenticate(self.student_user)
        self.assertEqual(self._attach(self.first).status_code, 403)

    def test_detach_preserves_history_and_only_removes_selected_links(self):
        self.assertEqual(self._attach(self.first, self.second).status_code, 200)
        link = self._link(self.first)
        other = self._link(self.second)
        history = VideoProgress.objects.create(video=link, enrollment=self.enrollment, progress=0.25)
        response = self.client.patch(
            self._path(), {"remove_video_ids": [self.original.pk, link.pk]}, format="json", **self._headers(),
        )
        self.assertEqual(response.status_code, 404)
        self.assertTrue(Video.objects.filter(pk=link.pk).exists())
        response = self.client.patch(
            self._path(), {"remove_video_ids": [link.pk]}, format="json", **self._headers(),
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertFalse(Video.objects.filter(pk=link.pk).exists())
        self.assertTrue(VideoProgress.objects.filter(pk=history.pk).exists())
        self.assertEqual(Video.objects.filter(pk__in=[self.original.pk, other.pk]).count(), 2)

    @patch("apps.infrastructure.storage.r2.delete_prefix_r2_video", return_value=2)
    def test_purge_defers_root_storage_until_last_use_expires(self, delete_prefix):
        self.assertEqual(self._attach(self.first).status_code, 200)
        link = self._link(self.first)
        cutoff = timezone.now() - timedelta(days=181)
        Video.objects.filter(pk=self.original.pk).update(deleted_at=cutoff)
        call_command("purge_deleted_videos", stdout=StringIO())
        delete_prefix.assert_not_called()
        self.assertTrue(Video.all_with_deleted.filter(pk=self.original.pk).exists())
        self.assertEqual(self._attach(self.second, source=link).status_code, 200)
        Video.objects.filter(reused_from=self.original).update(deleted_at=cutoff)
        call_command("purge_deleted_videos", stdout=StringIO())
        delete_prefix.assert_called_once_with(prefix=self.original.hls_path.rsplit("/", 1)[0] + "/")
        self.assertFalse(Video.all_with_deleted.filter(pk=self.original.pk).exists())
