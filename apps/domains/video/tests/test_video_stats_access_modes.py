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
