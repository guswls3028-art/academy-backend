from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIClient, APIRequestFactory, force_authenticate
from rest_framework_simplejwt.tokens import RefreshToken

from apps.core.models import Tenant, TenantMembership
from apps.domains.enrollment.models import Enrollment, SessionEnrollment
from apps.domains.lectures.models import Lecture, Session
from apps.domains.students.models import Student
from apps.domains.video.drm import verify_playback_token
from apps.domains.video.models import Video, VideoFolder
from apps.domains.video.views.playback_views import (
    PlaybackEndView,
    PlaybackEventBatchView,
    PlaybackHeartbeatView,
    PlaybackRefreshView,
    PlaybackStartView,
)


User = get_user_model()


class PlaybackStartStudentEnrollmentAccessTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.tenant = Tenant.objects.create(
            code="playback-start-access",
            name="Playback Start Access",
            is_active=True,
        )
        self.lecture = Lecture.objects.create(
            tenant=self.tenant,
            title="Shared Lecture",
            name="Shared Lecture",
            subject="MATH",
        )
        self.session = Session.objects.create(
            lecture=self.lecture,
            title="Shared Session",
            order=1,
        )
        self.video = Video.objects.create(
            tenant=self.tenant,
            session=self.session,
            title="Shared Video",
            status=Video.Status.READY,
            duration=100,
        )
        self.student_a, self.enrollment_a = self._create_student_enrollment("a", "A")
        self.student_b, self.enrollment_b = self._create_student_enrollment("b", "B")

    def _create_student_enrollment(self, suffix: str, name: str):
        user = User.objects.create_user(
            username=f"t{self.tenant.id}_playback-start-{suffix}",
            password="testpass123",
            tenant=self.tenant,
        )
        student = Student.objects.create(
            tenant=self.tenant,
            user=user,
            name=f"Student {name}",
            ps_number=f"PB-{suffix}",
            omr_code=f"PB{suffix.upper()}0000"[:8],
            parent_phone="01012345678",
            school_type="HIGH",
        )
        TenantMembership.ensure_active(tenant=self.tenant, user=user, role="student")
        enrollment = Enrollment.objects.create(
            tenant=self.tenant,
            student=student,
            lecture=self.lecture,
            status="ACTIVE",
        )
        SessionEnrollment.objects.create(
            tenant=self.tenant,
            session=self.session,
            enrollment=enrollment,
        )
        return student, enrollment

    def _post(self, *, student: Student, enrollment: Enrollment):
        request = self.factory.post(
            "/api/v1/video/playback/start/",
            {
                "video_id": self.video.id,
                "enrollment_id": enrollment.id,
                "device_id": "same-tenant-idor-regression",
            },
            format="json",
        )
        request.tenant = self.tenant
        force_authenticate(request, user=student.user)
        return PlaybackStartView.as_view()(request)

    def test_same_tenant_students_cannot_start_with_each_others_enrollment(self):
        response_a_using_b = self._post(
            student=self.student_a,
            enrollment=self.enrollment_b,
        )
        response_b_using_a = self._post(
            student=self.student_b,
            enrollment=self.enrollment_a,
        )

        self.assertEqual(response_a_using_b.status_code, 403, response_a_using_b.data)
        self.assertEqual(response_b_using_a.status_code, 403, response_b_using_a.data)

    def test_students_start_only_with_their_own_enrollment(self):
        response_a = self._post(student=self.student_a, enrollment=self.enrollment_a)
        response_b = self._post(student=self.student_b, enrollment=self.enrollment_b)

        self.assertEqual(response_a.status_code, 201, response_a.data)
        self.assertEqual(response_b.status_code, 201, response_b.data)
        valid_a, payload_a, error_a = verify_playback_token(response_a.data["token"])
        valid_b, payload_b, error_b = verify_playback_token(response_b.data["token"])
        self.assertTrue(valid_a, error_a)
        self.assertTrue(valid_b, error_b)
        self.assertEqual(payload_a["student_id"], self.student_a.id)
        self.assertEqual(payload_a["enrollment_id"], self.enrollment_a.id)
        self.assertEqual(payload_a["tenant_id"], self.tenant.id)
        self.assertEqual(payload_b["student_id"], self.student_b.id)
        self.assertEqual(payload_b["enrollment_id"], self.enrollment_b.id)
        self.assertEqual(payload_b["tenant_id"], self.tenant.id)

    def test_start_rejects_malformed_and_missing_video_ids_without_server_error(self):
        base = {
            "enrollment_id": self.enrollment_a.id,
            "device_id": "invalid-video-id",
        }
        for video_id, expected in (("not-an-id", 400), (-1, 400), (999999999, 404)):
            request = self.factory.post(
                "/api/v1/video/playback/start/",
                {**base, "video_id": video_id},
                format="json",
            )
            request.tenant = self.tenant
            force_authenticate(request, user=self.student_a.user)
            with self.subTest(video_id=video_id):
                response = PlaybackStartView.as_view()(request)
                self.assertEqual(response.status_code, expected, response.data)


class PlaybackFollowupTokenBindingTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.owner = User.objects.create_user(username="playback-token-owner")
        self.other = User.objects.create_user(username="playback-token-other")
        self.tenant = Tenant.objects.create(
            code="playback-followup-token",
            name="Playback Followup Token",
            is_active=True,
        )

    @patch("apps.domains.video.views.playback_views.verify_playback_token")
    def test_all_followup_endpoints_reject_token_owned_by_another_user(self, verify):
        verify.return_value = (True, {"user_id": self.owner.id}, None)
        cases = (
            ("refresh", PlaybackRefreshView.as_view(), {"token": "signed"}),
            ("heartbeat", PlaybackHeartbeatView.as_view(), {"token": "signed"}),
            ("end", PlaybackEndView.as_view(), {"token": "signed"}),
            ("events", PlaybackEventBatchView.as_view(), {"token": "signed", "events": []}),
        )

        for label, view, data in cases:
            request = self.factory.post("/api/v1/video/playback/followup/", data, format="json")
            force_authenticate(request, user=self.other)
            with self.subTest(view=label):
                response = view(request)
                self.assertEqual(response.status_code, 403, response.data)
                self.assertEqual(response.data["detail"], "token_user_mismatch")

    @patch("apps.domains.video.views.playback_views.verify_playback_token")
    def test_followups_reject_explicit_cross_tenant_token(self, verify):
        verify.return_value = (
            True,
            {"user_id": self.owner.id, "tenant_id": self.tenant.id + 1},
            None,
        )
        request = self.factory.post(
            "/api/v1/video/playback/end/",
            {"token": "signed"},
            format="json",
        )
        request.tenant = self.tenant
        force_authenticate(request, user=self.owner)

        response = PlaybackEndView.as_view()(request)

        self.assertEqual(response.status_code, 403, response.data)
        self.assertEqual(response.data["detail"], "token_tenant_mismatch")

    @patch("apps.domains.video.views.playback_views.verify_playback_token")
    @patch("apps.domains.video.views.playback_views.video_repo.video_get_by_id_with_relations")
    def test_legacy_token_is_bound_to_authoritative_video_tenant(self, get_video, verify):
        verify.return_value = (
            True,
            {"user_id": self.owner.id, "video_id": 17, "monitoring_enabled": False},
            None,
        )
        get_video.return_value = type("LegacyVideo", (), {"tenant_id": self.tenant.id})()
        request = self.factory.post(
            "/api/v1/video/playback/end/",
            {"token": "signed"},
            format="json",
        )
        request.tenant = self.tenant
        force_authenticate(request, user=self.owner)

        response = PlaybackEndView.as_view()(request)

        self.assertEqual(response.status_code, 200, response.data)


@override_settings(ALLOWED_HOSTS=["video-auth-guard", "testserver"])
class VideoManagementAuthenticationGuardTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(
            code="video-auth-guard",
            name="Video Auth Guard",
            is_active=True,
        )
        self.user = User.objects.create_user(
            username="video-auth-guard-admin",
            password="testpass123",
            tenant=self.tenant,
            is_staff=True,
        )
        self.membership = TenantMembership.ensure_active(
            tenant=self.tenant,
            user=self.user,
            role="admin",
        )
        self.path = "/api/v1/media/videos/folders/"

    def _access_token(self):
        token = RefreshToken.for_user(self.user).access_token
        token["token_version"] = self.user.token_version
        token["tenant_id"] = self.tenant.id
        return str(token)

    def _post_with_token(self, token):
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
        return client.post(
            self.path,
            {"name": "must-not-create"},
            format="json",
            HTTP_HOST=self.tenant.code,
        )

    def test_password_revoked_jwt_cannot_mutate_video_management(self):
        token = self._access_token()
        self.user.token_version += 1
        self.user.save(update_fields=["token_version"])

        response = self._post_with_token(token)

        self.assertEqual(response.status_code, 401, response.data)
        self.assertFalse(VideoFolder.objects.filter(tenant=self.tenant).exists())

    def test_membership_revoked_jwt_cannot_mutate_video_management(self):
        token = self._access_token()
        self.membership.is_active = False
        self.membership.save(update_fields=["is_active"])

        response = self._post_with_token(token)

        self.assertEqual(response.status_code, 401, response.data)
        self.assertFalse(VideoFolder.objects.filter(tenant=self.tenant).exists())

    def test_session_post_without_csrf_token_is_rejected(self):
        client = APIClient(enforce_csrf_checks=True)
        client.force_login(self.user)

        response = client.post(
            self.path,
            {"name": "must-not-create"},
            format="json",
            HTTP_HOST=self.tenant.code,
        )

        self.assertEqual(response.status_code, 403, response.data)
        self.assertFalse(VideoFolder.objects.filter(tenant=self.tenant).exists())

    def test_membership_revoked_session_with_valid_csrf_is_rejected(self):
        self.membership.is_active = False
        self.membership.save(update_fields=["is_active"])
        client = APIClient(enforce_csrf_checks=True)
        client.force_login(self.user)
        csrf_secret = "a" * 32
        client.cookies["csrftoken"] = csrf_secret

        response = client.post(
            self.path,
            {"name": "must-not-create"},
            format="json",
            HTTP_HOST=self.tenant.code,
            HTTP_X_CSRFTOKEN=csrf_secret,
        )

        self.assertEqual(response.status_code, 401, response.data)
        self.assertIn("권한", str(response.data["detail"]))
        self.assertFalse(VideoFolder.objects.filter(tenant=self.tenant).exists())
