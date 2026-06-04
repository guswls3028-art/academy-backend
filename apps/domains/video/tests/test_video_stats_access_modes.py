from __future__ import annotations

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.models import Tenant, TenantMembership
from apps.domains.attendance.models import Attendance
from apps.domains.enrollment.models import Enrollment
from apps.domains.lectures.models import Lecture, Session
from apps.domains.students.models import Student
from apps.domains.video.models import AccessMode, Video, VideoAccess, VideoProgress
from apps.domains.video.views.achievement_views import VideoAchievementView
from apps.domains.video.views.permission_views import VideoPermissionViewSet
from apps.domains.video.views.video_policy_impact import VideoPolicyImpactAPIView
from apps.domains.video.views.video_views import VideoViewSet


User = get_user_model()


class VideoStatsAccessModeTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.tenant = Tenant.objects.create(
            name="Video Stats Tenant",
            code="video-stats",
            is_active=True,
        )
        self.user = User.objects.create_user(
            username="video_stats_admin",
            password="test1234",
            tenant=self.tenant,
            is_staff=True,
        )
        TenantMembership.ensure_active(tenant=self.tenant, user=self.user, role="admin")

        self.lecture = Lecture.objects.create(
            tenant=self.tenant,
            title="Stats Lecture",
            name="Stats Lecture",
            subject="MATH",
        )
        self.session = Session.objects.create(
            lecture=self.lecture,
            title="Stats Session",
            order=1,
        )
        self.video = Video.objects.create(
            tenant=self.tenant,
            session=self.session,
            title="Stats Video",
            file_key="videos/raw/stats.mp4",
            duration=120,
            status=Video.Status.READY,
        )

    def _create_enrollment(self, index: int) -> Enrollment:
        student_user = User.objects.create_user(
            username=f"video_stats_student_{index}",
            password="test1234",
            tenant=self.tenant,
        )
        student = Student.objects.create(
            tenant=self.tenant,
            user=student_user,
            ps_number=f"VS-{index}",
            omr_code=f"{index:08d}"[-8:],
            name=f"Stats Student {index}",
            parent_phone=f"0101234{index:04d}",
        )
        return Enrollment.objects.create(
            tenant=self.tenant,
            student=student,
            lecture=self.lecture,
            status="ACTIVE",
        )

    def _call_stats(self):
        request = self.factory.get(f"/api/v1/media/videos/{self.video.id}/stats/")
        request.tenant = self.tenant
        force_authenticate(request, user=self.user)
        view = VideoViewSet.as_view({"get": "stats"})
        return view(request, pk=self.video.id)

    def _call_summary(self):
        request = self.factory.get(f"/api/v1/media/videos/{self.video.id}/summary/")
        request.tenant = self.tenant
        force_authenticate(request, user=self.user)
        view = VideoViewSet.as_view({"get": "summary"})
        return view(request, pk=self.video.id)

    def _call_achievement(self):
        request = self.factory.get(f"/api/v1/media/videos/{self.video.id}/achievement/")
        request.tenant = self.tenant
        force_authenticate(request, user=self.user)
        return VideoAchievementView.as_view()(request, video_id=self.video.id)

    def test_stats_uses_prefetched_access_inputs_without_single_row_queries(self):
        proctored = self._create_enrollment(1)
        completed = self._create_enrollment(2)
        blocked = self._create_enrollment(3)

        Attendance.objects.create(
            tenant=self.tenant,
            session=self.session,
            enrollment=proctored,
            status="ONLINE",
        )
        Attendance.objects.create(
            tenant=self.tenant,
            session=self.session,
            enrollment=completed,
            status="ONLINE",
        )
        Attendance.objects.create(
            tenant=self.tenant,
            session=self.session,
            enrollment=blocked,
            status="PRESENT",
        )
        VideoProgress.objects.create(
            video=self.video,
            enrollment=completed,
            progress=0.95,
            completed=False,
        )
        VideoAccess.objects.create(
            video=self.video,
            enrollment=blocked,
            rule="blocked",
            access_mode=AccessMode.BLOCKED,
        )

        with (
            patch(
                "apps.domains.results.utils.clinic_highlight.compute_clinic_highlight_map",
                return_value={},
            ),
            patch(
                "academy.adapters.db.django.repositories_video.video_access_get",
                side_effect=AssertionError("stats must not issue per-student VideoAccess queries"),
            ),
            patch(
                "academy.adapters.db.django.repositories_video.video_progress_get",
                side_effect=AssertionError("stats must not issue per-student VideoProgress queries"),
            ),
            patch(
                "academy.adapters.db.django.repositories_video.attendance_filter_session_enrollment",
                side_effect=AssertionError("stats must not issue per-student Attendance queries"),
            ),
        ):
            response = self._call_stats()

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["total_filtered"], 3)

        modes_by_student = {
            row["student_name"]: row["access_mode"]
            for row in response.data["students"]
        }
        self.assertEqual(modes_by_student["Stats Student 1"], AccessMode.PROCTORED_CLASS.value)
        self.assertEqual(modes_by_student["Stats Student 2"], AccessMode.FREE_REVIEW.value)
        self.assertEqual(modes_by_student["Stats Student 3"], AccessMode.BLOCKED.value)

    def test_stats_and_summary_use_domain_completion_threshold(self):
        enrollment = self._create_enrollment(4)
        VideoProgress.objects.create(
            video=self.video,
            enrollment=enrollment,
            progress=0.9,
            completed=False,
        )
        VideoAccess.objects.create(
            video=self.video,
            enrollment=enrollment,
            rule="once",
            access_mode=AccessMode.PROCTORED_CLASS,
        )

        stats_response = self._call_stats()
        summary_response = self._call_summary()

        self.assertEqual(stats_response.status_code, 200, stats_response.data)
        row = stats_response.data["students"][0]
        self.assertTrue(row["completed"])
        self.assertEqual(row["effective_rule"], "free")

        self.assertEqual(summary_response.status_code, 200, summary_response.data)
        self.assertEqual(summary_response.data["completed_count"], 1)
        self.assertEqual(summary_response.data["completion_rate"], 1.0)

    def test_summary_requires_staff_membership(self):
        non_staff = User.objects.create_user(
            username="video_stats_non_staff",
            password="test1234",
            tenant=self.tenant,
        )
        request = self.factory.get(f"/api/v1/media/videos/{self.video.id}/summary/")
        request.tenant = self.tenant
        force_authenticate(request, user=non_staff)

        response = VideoViewSet.as_view({"get": "summary"})(request, pk=self.video.id)

        self.assertEqual(response.status_code, 403)

    def test_policy_impact_uses_prefetched_access_inputs(self):
        proctored = self._create_enrollment(5)
        completed = self._create_enrollment(6)

        Attendance.objects.create(
            tenant=self.tenant,
            session=self.session,
            enrollment=proctored,
            status="ONLINE",
        )
        Attendance.objects.create(
            tenant=self.tenant,
            session=self.session,
            enrollment=completed,
            status="ONLINE",
        )
        VideoProgress.objects.create(
            video=self.video,
            enrollment=completed,
            progress=0.9,
            completed=False,
        )

        request = self.factory.get(f"/api/v1/media/videos/{self.video.id}/policy-impact/")
        request.tenant = self.tenant
        force_authenticate(request, user=self.user)

        with (
            patch(
                "academy.adapters.db.django.repositories_video.video_access_get",
                side_effect=AssertionError("policy impact must not issue per-student VideoAccess queries"),
            ),
            patch(
                "academy.adapters.db.django.repositories_video.video_progress_get",
                side_effect=AssertionError("policy impact must not issue per-student VideoProgress queries"),
            ),
            patch(
                "academy.adapters.db.django.repositories_video.attendance_filter_session_enrollment",
                side_effect=AssertionError("policy impact must not issue per-student Attendance queries"),
            ),
        ):
            response = VideoPolicyImpactAPIView.as_view()(request, video_id=self.video.id)

        self.assertEqual(response.status_code, 200, response.data)
        modes_by_student = {
            row["student_name"]: row["access_mode"]
            for row in response.data
        }
        self.assertEqual(modes_by_student["Stats Student 5"], AccessMode.PROCTORED_CLASS.value)
        self.assertEqual(modes_by_student["Stats Student 6"], AccessMode.FREE_REVIEW.value)

    def test_bulk_permission_set_keeps_legacy_rule_in_sync_with_access_mode(self):
        enrollment = self._create_enrollment(7)
        request = self.factory.post(
            "/api/v1/media/video-permissions/bulk_set/",
            {
                "video_id": self.video.id,
                "enrollments": [enrollment.id],
                "access_mode": AccessMode.PROCTORED_CLASS.value,
            },
            format="json",
        )
        request.tenant = self.tenant
        force_authenticate(request, user=self.user)

        response = VideoPermissionViewSet.as_view({"post": "bulk_set"})(request)

        self.assertEqual(response.status_code, 200, response.data)
        access = VideoAccess.objects.get(video=self.video, enrollment=enrollment)
        self.assertEqual(access.access_mode, AccessMode.PROCTORED_CLASS)
        self.assertEqual(access.rule, "once")

    def test_achievement_uses_domain_completion_threshold(self):
        enrollment = self._create_enrollment(8)
        Attendance.objects.create(
            tenant=self.tenant,
            session=self.session,
            enrollment=enrollment,
            status="ONLINE",
        )
        VideoProgress.objects.create(
            video=self.video,
            enrollment=enrollment,
            progress=0.9,
            completed=False,
        )

        response = self._call_achievement()

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["summary"]["completed_rate"], 100.0)
        self.assertEqual(response.data["summary"]["incomplete_count"], 0)
        self.assertTrue(response.data["students"][0]["completed"])
        self.assertEqual(response.data["students"][0]["status"], "completed")
