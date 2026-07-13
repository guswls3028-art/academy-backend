from django.test import TestCase, override_settings
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.models import Tenant, TenantMembership, User
from apps.domains.attendance.models import Attendance
from apps.domains.enrollment.models import Enrollment
from apps.domains.lectures.models import Lecture, Session
from apps.domains.parents.models import Parent
from apps.domains.student_app.media.views import (
    StudentPublicSessionView,
    StudentVideoCommentListView,
    StudentVideoLikeView,
    StudentVideoMeView,
    StudentVideoPlaybackView,
    StudentVideoProgressView,
    StudentSessionVideoListView,
    StudentVideoStatsView,
)
from apps.domains.students.models import Student
from apps.domains.video.models import (
    AccessMode,
    Video,
    VideoAccess,
    VideoComment,
    VideoLike,
    VideoPlaybackSession,
    VideoProgress,
)


class StudentVideoProgressEnrollmentResolutionTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.tenant = Tenant.objects.create(
            code="student-video-progress",
            name="Student Video Progress",
            is_active=True,
        )
        self.user = User.objects.create_user(
            username="student-video-progress-user",
            password="testpass123",
            tenant=self.tenant,
        )
        TenantMembership.ensure_active(tenant=self.tenant, user=self.user, role="student")
        self.parent_user = User.objects.create_user(
            username="student-video-progress-parent",
            password="testpass123",
            tenant=self.tenant,
        )
        TenantMembership.ensure_active(tenant=self.tenant, user=self.parent_user, role="parent")
        self.parent = Parent.objects.create(
            tenant=self.tenant,
            user=self.parent_user,
            name="Video Parent",
            phone="01099998888",
        )
        self.student = Student.objects.create(
            tenant=self.tenant,
            user=self.user,
            parent=self.parent,
            name="Video Student",
            ps_number="SVP-001",
            omr_code="12345678",
            parent_phone="01012345678",
            school_type="HIGH",
        )
        self.old_lecture = Lecture.objects.create(
            tenant=self.tenant,
            title="Old Lecture",
            name="Old Lecture",
            subject="MATH",
        )
        self.target_lecture = Lecture.objects.create(
            tenant=self.tenant,
            title="Target Lecture",
            name="Target Lecture",
            subject="MATH",
        )
        self.old_enrollment = Enrollment.objects.create(
            tenant=self.tenant,
            student=self.student,
            lecture=self.old_lecture,
            status="ACTIVE",
        )
        self.target_enrollment = Enrollment.objects.create(
            tenant=self.tenant,
            student=self.student,
            lecture=self.target_lecture,
            status="ACTIVE",
        )
        self.target_session = Session.objects.create(
            lecture=self.target_lecture,
            title="Target Session",
            order=1,
        )
        self.video = Video.objects.create(
            tenant=self.tenant,
            session=self.target_session,
            title="Target Video",
            status=Video.Status.READY,
            duration=100,
        )

    def _post_progress(self, payload, *, user=None, selected_student_id=None, video=None):
        target_video = video or self.video
        request = self.factory.post(
            f"/api/v1/student/video/videos/{target_video.id}/progress/",
            payload,
            format="json",
        )
        if selected_student_id is not None:
            request.META["HTTP_X_STUDENT_ID"] = str(selected_student_id)
        request.tenant = self.tenant
        force_authenticate(request, user=user or self.user)
        return StudentVideoProgressView.as_view()(request, video_id=target_video.id)

    def _get_playback(self, *, user=None, enrollment_id=None, selected_student_id=None):
        path = f"/api/v1/student/video/videos/{self.video.id}/playback/"
        if enrollment_id is not None:
            path += f"?enrollment={enrollment_id}"
        request = self.factory.get(path)
        if selected_student_id is not None:
            request.META["HTTP_X_STUDENT_ID"] = str(selected_student_id)
        request.tenant = self.tenant
        force_authenticate(request, user=user or self.user)
        return StudentVideoPlaybackView.as_view()(request, video_id=self.video.id)

    def _get_me_stats(self, *, user=None, selected_student_id=None):
        request = self.factory.get("/api/v1/student/video/me/stats/")
        if selected_student_id is not None:
            request.META["HTTP_X_STUDENT_ID"] = str(selected_student_id)
        request.tenant = self.tenant
        force_authenticate(request, user=user or self.user)
        return StudentVideoStatsView.as_view()(request)

    def _get_me(self, *, user=None, selected_student_id=None):
        request = self.factory.get("/api/v1/student/video/me/")
        if selected_student_id is not None:
            request.META["HTTP_X_STUDENT_ID"] = str(selected_student_id)
        request.tenant = self.tenant
        force_authenticate(request, user=user or self.user)
        return StudentVideoMeView.as_view()(request)

    def _get_session_videos(self, *, user=None, enrollment_id=None, selected_student_id=None):
        path = f"/api/v1/student/video/sessions/{self.target_session.id}/videos/"
        if enrollment_id is not None:
            path += f"?enrollment={enrollment_id}"
        request = self.factory.get(path)
        if selected_student_id is not None:
            request.META["HTTP_X_STUDENT_ID"] = str(selected_student_id)
        request.tenant = self.tenant
        force_authenticate(request, user=user or self.user)
        return StudentSessionVideoListView.as_view()(request, session_id=self.target_session.id)

    def _get_public_session(self, *, user=None, selected_student_id=None):
        request = self.factory.get("/api/v1/student/video/public-session/")
        if selected_student_id is not None:
            request.META["HTTP_X_STUDENT_ID"] = str(selected_student_id)
        request.tenant = self.tenant
        force_authenticate(request, user=user or self.user)
        return StudentPublicSessionView.as_view()(request)

    def _post_like(self, *, user=None, selected_student_id=None):
        request = self.factory.post(
            f"/api/v1/student/video/videos/{self.video.id}/like/",
            {},
            format="json",
        )
        if selected_student_id is not None:
            request.META["HTTP_X_STUDENT_ID"] = str(selected_student_id)
        request.tenant = self.tenant
        force_authenticate(request, user=user or self.user)
        return StudentVideoLikeView.as_view()(request, video_id=self.video.id)

    def _post_comment(self, *, user=None, selected_student_id=None):
        request = self.factory.post(
            f"/api/v1/student/video/videos/{self.video.id}/comments/",
            {"content": "must not be written"},
            format="json",
        )
        if selected_student_id is not None:
            request.META["HTTP_X_STUDENT_ID"] = str(selected_student_id)
        request.tenant = self.tenant
        force_authenticate(request, user=user or self.user)
        return StudentVideoCommentListView.as_view()(request, video_id=self.video.id)

    def _create_parent_child(self, suffix: str):
        child_user = User.objects.create_user(
            username=f"student-video-progress-child-{suffix}",
            password="testpass123",
            tenant=self.tenant,
        )
        TenantMembership.ensure_active(tenant=self.tenant, user=child_user, role="student")
        return Student.objects.create(
            tenant=self.tenant,
            user=child_user,
            parent=self.parent,
            name=f"Video Child {suffix}",
            ps_number=f"SVP-{suffix}",
            omr_code=f"8765{suffix.zfill(4)}",
            parent_phone="01012345678",
            school_type="HIGH",
        )

    def _create_unowned_student(self):
        child_user = User.objects.create_user(
            username="student-video-progress-unowned",
            password="testpass123",
            tenant=self.tenant,
        )
        TenantMembership.ensure_active(tenant=self.tenant, user=child_user, role="student")
        return Student.objects.create(
            tenant=self.tenant,
            user=child_user,
            name="Unowned Video Student",
            ps_number="SVP-UNOWNED",
            omr_code="SVUNOWND",
            parent_phone="01012345678",
            school_type="HIGH",
        )

    def test_invalid_parent_child_headers_fail_closed_across_media_reads(self):
        unowned_student = self._create_unowned_student()

        for raw_student_id in ("not-a-student-id", unowned_student.id, ""):
            with self.subTest(raw_student_id=raw_student_id):
                responses = [
                    self._get_public_session(
                        user=self.parent_user,
                        selected_student_id=raw_student_id,
                    ),
                    self._get_me(
                        user=self.parent_user,
                        selected_student_id=raw_student_id,
                    ),
                    self._get_me_stats(
                        user=self.parent_user,
                        selected_student_id=raw_student_id,
                    ),
                    self._get_session_videos(
                        user=self.parent_user,
                        selected_student_id=raw_student_id,
                    ),
                    self._get_playback(
                        user=self.parent_user,
                        enrollment_id=self.target_enrollment.id,
                        selected_student_id=raw_student_id,
                    ),
                ]
                for response in responses:
                    self.assertGreaterEqual(response.status_code, 400, response.data)
                    self.assertLess(response.status_code, 500, response.data)

                self.video.refresh_from_db()
                self.assertEqual(self.video.view_count, 0)
                self.assertFalse(
                    Lecture.objects.filter(tenant=self.tenant, is_system=True).exists()
                )
                self.assertFalse(VideoPlaybackSession.objects.exists())

    def test_invalid_parent_child_headers_reject_media_writes_without_mutation(self):
        unowned_student = self._create_unowned_student()

        for raw_student_id in ("not-a-student-id", unowned_student.id, ""):
            with self.subTest(raw_student_id=raw_student_id):
                responses = [
                    self._post_progress(
                        {
                            "enrollment_id": self.target_enrollment.id,
                            "progress": 50,
                        },
                        user=self.parent_user,
                        selected_student_id=raw_student_id,
                    ),
                    self._post_like(
                        user=self.parent_user,
                        selected_student_id=raw_student_id,
                    ),
                    self._post_comment(
                        user=self.parent_user,
                        selected_student_id=raw_student_id,
                    ),
                ]
                for response in responses:
                    self.assertGreaterEqual(response.status_code, 400, response.data)
                    self.assertLess(response.status_code, 500, response.data)

                self.assertFalse(VideoProgress.objects.exists())
                self.assertFalse(VideoLike.objects.exists())
                self.assertFalse(VideoComment.objects.exists())
                self.video.refresh_from_db()
                self.assertEqual(self.video.like_count, 0)
                self.assertEqual(self.video.comment_count, 0)

    def test_malformed_explicit_enrollment_rejects_without_fallback_or_mutation(self):
        invalid_values = (
            "not-an-enrollment",
            "",
            self.target_enrollment.id + 0.5,
        )

        for invalid_value in invalid_values:
            with self.subTest(invalid_value=invalid_value):
                session_response = self._get_session_videos(enrollment_id=invalid_value)
                playback_response = self._get_playback(enrollment_id=invalid_value)
                progress_response = self._post_progress(
                    {"enrollment_id": invalid_value, "progress": 50}
                )

                self.assertEqual(session_response.status_code, 400, session_response.data)
                self.assertEqual(playback_response.status_code, 400, playback_response.data)
                self.assertEqual(progress_response.status_code, 400, progress_response.data)
                self.assertFalse(VideoProgress.objects.exists())
                self.assertFalse(VideoPlaybackSession.objects.exists())
                self.video.refresh_from_db()
                self.assertEqual(self.video.view_count, 0)

    def test_progress_without_explicit_enrollment_uses_video_lecture_enrollment(self):
        response = self._post_progress({
            "progress": 50,
            "last_position": 37,
            "completed": False,
        })

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["enrollment_id"], self.target_enrollment.id)

        progress = VideoProgress.objects.get(video=self.video)
        self.assertEqual(progress.enrollment_id, self.target_enrollment.id)
        self.assertEqual(progress.last_position, 37)
        self.assertAlmostEqual(progress.progress, 0.5)
        self.assertFalse(
            VideoProgress.objects.filter(
                video=self.video,
                enrollment=self.old_enrollment,
            ).exists()
        )

    def test_progress_response_uses_domain_completion_threshold(self):
        response = self._post_progress({
            "progress": 90,
            "last_position": 90,
            "completed": False,
        })

        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(response.data["completed"])

    def test_public_video_progress_persists_with_hidden_enrollment(self):
        public_lecture = Lecture.get_or_create_system_lecture(self.tenant)
        public_video = Video.objects.create(
            tenant=self.tenant,
            session=None,
            title="Public Progress Video",
            status=Video.Status.READY,
            visibility=Video.Visibility.PUBLIC,
            duration=100,
        )

        response = self._post_progress(
            {"progress": 90, "last_position": 90, "completed": False},
            video=public_video,
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertNotEqual(response.data["enrollment_id"], 0)
        self.assertTrue(response.data["completed"])
        public_video.refresh_from_db()
        self.assertEqual(public_video.session.lecture_id, public_lecture.id)
        self.assertTrue(
            VideoProgress.objects.filter(
                video=public_video,
                enrollment_id=response.data["enrollment_id"],
            ).exists()
        )

        me_response = self._get_me()
        self.assertEqual(me_response.status_code, 200, me_response.data)
        self.assertEqual(me_response.data["public"]["lecture_id"], public_lecture.id)
        self.assertNotIn(
            public_lecture.id,
            [lecture["id"] for lecture in me_response.data["lectures"]],
        )

        stats_response = self._get_me_stats()
        self.assertEqual(stats_response.status_code, 200, stats_response.data)
        self.assertEqual(stats_response.data["total_videos"], 2)
        self.assertEqual(stats_response.data["completed_videos"], 1)

    def test_student_stats_uses_domain_completion_threshold(self):
        VideoProgress.objects.create(
            video=self.video,
            enrollment=self.target_enrollment,
            progress=0.9,
            completed=False,
        )

        response = self._get_me_stats()

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["total_videos"], 1)
        self.assertEqual(response.data["completed_videos"], 1)
        self.assertEqual(response.data["completion_rate"], 100)
        self.assertEqual(response.data["lectures"][0]["completed_count"], 1)

    def test_student_stats_counts_ready_videos_without_progress(self):
        Video.objects.create(
            tenant=self.tenant,
            session=self.target_session,
            title="Unwatched Target Video",
            status=Video.Status.READY,
            duration=200,
            order=2,
        )
        VideoProgress.objects.create(
            video=self.video,
            enrollment=self.target_enrollment,
            progress=0.9,
            completed=False,
        )

        response = self._get_me_stats()

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["total_videos"], 2)
        self.assertEqual(response.data["completed_videos"], 1)
        self.assertEqual(response.data["completion_rate"], 50)
        self.assertEqual(response.data["total_watch_duration"], 90)
        self.assertEqual(response.data["total_content_duration"], 300)
        self.assertEqual(response.data["lectures"][0]["video_count"], 2)
        self.assertEqual(response.data["lectures"][0]["completed_count"], 1)
        self.assertEqual(response.data["lectures"][0]["progress_pct"], 50)

    def test_video_me_hides_inactive_enrollment_lecture(self):
        self.target_enrollment.status = "INACTIVE"
        self.target_enrollment.save(update_fields=["status", "updated_at"])

        response = self._get_me()

        self.assertEqual(response.status_code, 200, response.data)
        self.assertNotIn(
            self.target_lecture.id,
            [lecture["id"] for lecture in response.data["lectures"]],
        )

    def test_student_stats_ignore_inactive_enrollment_videos_and_progress(self):
        VideoProgress.objects.create(
            video=self.video,
            enrollment=self.target_enrollment,
            progress=0.9,
            completed=False,
        )
        self.target_enrollment.status = "INACTIVE"
        self.target_enrollment.save(update_fields=["status", "updated_at"])

        response = self._get_me_stats()

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["total_videos"], 0)
        self.assertEqual(response.data["completed_videos"], 0)
        self.assertEqual(response.data["lectures"], [])

    def test_session_video_list_uses_prefetched_completion_and_access_modes(self):
        second_video = Video.objects.create(
            tenant=self.tenant,
            session=self.target_session,
            title="Second Target Video",
            status=Video.Status.READY,
            duration=100,
            order=2,
        )
        Attendance.objects.create(
            tenant=self.tenant,
            session=self.target_session,
            enrollment=self.target_enrollment,
            status="ONLINE",
        )
        VideoProgress.objects.create(
            video=self.video,
            enrollment=self.target_enrollment,
            progress=0.9,
            completed=False,
        )

        response = self._get_session_videos(enrollment_id=self.target_enrollment.id)

        self.assertEqual(response.status_code, 200, response.data)
        rows = {row["id"]: row for row in response.data["items"]}
        self.assertTrue(rows[self.video.id]["completed"])
        self.assertEqual(rows[self.video.id]["access_mode"], AccessMode.FREE_REVIEW.value)
        self.assertFalse(rows[second_video.id]["completed"])
        self.assertEqual(rows[second_video.id]["access_mode"], AccessMode.PROCTORED_CLASS.value)

    def test_progress_body_enrollment_id_is_validated_against_video_lecture(self):
        response = self._post_progress({
            "enrollment_id": self.old_enrollment.id,
            "progress": 50,
        })

        self.assertEqual(response.status_code, 400)
        self.assertFalse(VideoProgress.objects.filter(video=self.video).exists())

    def test_blocked_access_mode_rejects_progress_even_when_legacy_rule_is_free(self):
        VideoAccess.objects.create(
            video=self.video,
            enrollment=self.target_enrollment,
            rule="free",
            access_mode=AccessMode.BLOCKED,
        )

        response = self._post_progress({"progress": 50})

        self.assertEqual(response.status_code, 403)
        self.assertFalse(VideoProgress.objects.filter(video=self.video).exists())

    def test_blocked_access_mode_rejects_playback_even_when_legacy_rule_is_free(self):
        VideoAccess.objects.create(
            video=self.video,
            enrollment=self.target_enrollment,
            rule="free",
            access_mode=AccessMode.BLOCKED,
        )

        response = self._get_playback(enrollment_id=self.target_enrollment.id)

        self.assertEqual(response.status_code, 403)

    @override_settings(CDN_HLS_BASE_URL="https://cdn.example.test", CDN_HLS_SIGNING_SECRET="")
    def test_proctored_playback_issues_session_with_aware_expiry(self):
        Attendance.objects.create(
            tenant=self.tenant,
            session=self.target_session,
            enrollment=self.target_enrollment,
            status="ONLINE",
        )

        response = self._get_playback(enrollment_id=self.target_enrollment.id)

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["video"]["access_mode"], AccessMode.PROCTORED_CLASS.value)
        self.assertEqual(response.data["policy"]["access_mode"], AccessMode.PROCTORED_CLASS.value)
        self.assertIsNotNone(response.data["playback_session_id"])
        self.assertIsNotNone(response.data["playback_token"])
        session = VideoPlaybackSession.objects.get(session_id=response.data["playback_session_id"])
        self.assertEqual(session.video_id, self.video.id)
        self.assertEqual(session.enrollment_id, self.target_enrollment.id)
        self.assertIsNotNone(session.expires_at.tzinfo)

    def test_parent_progress_echo_requires_selected_child_video_enrollment(self):
        unlinked_parent_user = User.objects.create_user(
            username="student-video-unlinked-parent",
            password="testpass123",
            tenant=self.tenant,
        )
        TenantMembership.ensure_active(tenant=self.tenant, user=unlinked_parent_user, role="parent")
        Parent.objects.create(
            tenant=self.tenant,
            user=unlinked_parent_user,
            name="Unlinked Parent",
            phone="01055556666",
        )

        response = self._post_progress({"progress": 50}, user=unlinked_parent_user)

        self.assertEqual(response.status_code, 403)
        self.assertFalse(VideoProgress.objects.filter(video=self.video).exists())

    def test_parent_progress_echo_uses_child_video_enrollment_without_saving(self):
        response = self._post_progress(
            {"progress": 90, "last_position": 90, "completed": True},
            user=self.parent_user,
            selected_student_id=self.student.id,
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["enrollment_id"], self.target_enrollment.id)
        self.assertEqual(response.data["progress_percent"], 90)
        self.assertTrue(response.data["completed"])
        self.assertFalse(VideoProgress.objects.filter(video=self.video).exists())

    def test_parent_progress_echo_uses_domain_completion_threshold(self):
        response = self._post_progress(
            {"progress": 90, "last_position": 90, "completed": False},
            user=self.parent_user,
            selected_student_id=self.student.id,
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["enrollment_id"], self.target_enrollment.id)
        self.assertTrue(response.data["completed"])
        self.assertFalse(VideoProgress.objects.filter(video=self.video).exists())

    def test_parent_progress_echo_finds_child_enrollment_when_default_child_differs(self):
        self._create_parent_child("002")

        response = self._post_progress(
            {"progress": 90, "last_position": 90, "completed": True},
            user=self.parent_user,
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["enrollment_id"], self.target_enrollment.id)
        self.assertFalse(VideoProgress.objects.filter(video=self.video).exists())

    def test_parent_progress_echo_accepts_explicit_child_enrollment_without_saving(self):
        response = self._post_progress(
            {"enrollment_id": self.target_enrollment.id, "progress": 90, "completed": True},
            user=self.parent_user,
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["enrollment_id"], self.target_enrollment.id)
        self.assertFalse(VideoProgress.objects.filter(video=self.video).exists())

    def test_parent_progress_echo_rejects_explicit_enrollment_for_different_selected_child(self):
        other_child = self._create_parent_child("003")

        response = self._post_progress(
            {"enrollment_id": self.target_enrollment.id, "progress": 90, "completed": True},
            user=self.parent_user,
            selected_student_id=other_child.id,
        )

        self.assertEqual(response.status_code, 403)
        self.assertFalse(VideoProgress.objects.filter(video=self.video).exists())
