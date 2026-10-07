"""Focused student-video regressions; fixture setup reuses the enrollment owner."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from types import SimpleNamespace
from unittest import skipUnless
from unittest.mock import patch

from django.db import close_old_connections, connection, connections
from django.test import SimpleTestCase, TestCase, TransactionTestCase, override_settings
from rest_framework.test import APIRequestFactory, force_authenticate

from tests import test_student_video_progress_enrollment_resolution as fixtures
from apps.core.models import Tenant, TenantMembership, User
from apps.domains.attendance.models import Attendance
from apps.domains.video.views.progress_views import VideoProgressViewSet
from apps.domains.video.services.access_resolver import resolve_access_modes_for_videos_prefetched
from apps.domains.student_app.media.views import (
    StudentVideoCommentDetailView, StudentVideoCommentListView,
)
from apps.domains.video.models import AccessMode, Video, VideoAccess, VideoComment, VideoProgress
from apps.domains.video.services.access_resolver import (
    _resolve_access_mode_loaded, get_effective_access_mode, resolve_access_mode,
)
from apps.domains.video.services.inactive_entitlements import update_inactive_entitled_video_progress
from apps.domains.video.services.playback_policy import build_effective_playback_policy


class StudentVideoPolicyRegressionTests(SimpleTestCase):
    def test_explicit_speed_block_survives_mode_defaults(self):
        video = SimpleNamespace(allow_skip=False, max_speed=2.0, show_watermark=True, duration=100)
        for mode in AccessMode:
            for override in (None, 1.5):
                with self.subTest(mode=mode, override=override):
                    permission = SimpleNamespace(
                        allow_skip_override=None, max_speed_override=override,
                        show_watermark_override=None, block_seek=True, block_speed_control=True,
                    )
                    policy = build_effective_playback_policy(video=video, access_mode=mode, permission=permission)
                    self.assertEqual(policy["playback_rate"], {"max": 1.0, "ui_control": False})
                    self.assertFalse(policy["allow_seek"])
                    self.assertEqual(policy["seek"]["mode"], "blocked")
                    self.assertTrue(policy["watermark"]["enabled"])

    def test_unblocked_speed_keeps_existing_mode_limits(self):
        video = SimpleNamespace(allow_skip=False, max_speed=2.0, show_watermark=True, duration=100)
        for mode in (AccessMode.PROCTORED_CLASS, AccessMode.FREE_REVIEW):
            for override, expected in ((None, 2.0), (1.25, 1.25)):
                with self.subTest(mode=mode, override=override):
                    permission = SimpleNamespace(
                        allow_skip_override=None, max_speed_override=override,
                        show_watermark_override=None, block_seek=False, block_speed_control=False,
                    )
                    policy = build_effective_playback_policy(video=video, access_mode=mode, permission=permission)
                    self.assertEqual(policy["playback_rate"], {"max": expected, "ui_control": True})

    def test_prefetched_access_honors_both_override_directions(self):
        for attendance, override in (("PRESENT", AccessMode.PROCTORED_CLASS), ("ONLINE", AccessMode.FREE_REVIEW)):
            with self.subTest(attendance=attendance, override=override):
                permission = SimpleNamespace(access_mode=override, is_override=True, rule="allowed", proctored_completed_at=None)
                actual = _resolve_access_mode_loaded(perm=permission, attendance_status=attendance, progress=None)
                self.assertEqual(actual, override)

    def test_block_and_completion_keep_precedence_over_override(self):
        permission = SimpleNamespace(access_mode=AccessMode.PROCTORED_CLASS, is_override=True, rule="blocked", proctored_completed_at=None)
        progress = SimpleNamespace(progress=0.95, completed=False)
        self.assertEqual(_resolve_access_mode_loaded(perm=permission, attendance_status="ONLINE", progress=progress), AccessMode.BLOCKED)
        permission.rule = "allowed"
        self.assertEqual(_resolve_access_mode_loaded(perm=permission, attendance_status="PRESENT", progress=progress), AccessMode.FREE_REVIEW)


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class StudentVideoContractRegressionTests(TestCase):
    def setUp(self):
        self.fixture = fixtures.StudentVideoProgressEnrollmentResolutionTests(methodName="runTest")
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.video = Video.objects.get(session=self.fixture.target_session)
        initial = self.fixture._post_progress({"progress": 0, "completed": False, "last_position": 0})
        self.assertEqual(initial.status_code, 200, initial.data)
        self.enrollment_id = initial.data["enrollment_id"]
        root_response = self.fixture._post_comment()
        self.assertEqual(root_response.status_code, 201, root_response.data)
        self.root_comment = VideoComment.objects.select_related("author_student__user").get(pk=root_response.data["id"])
        self.actor = self.root_comment.author_student.user
        self.factory = APIRequestFactory()

    def _comment_request(self, method, payload=None, comment_id=None, video=None):
        video = video or self.video
        request = getattr(self.factory, method)("/student/video/comments/", payload or {}, format="json")
        request.tenant = self.video.tenant
        force_authenticate(request, user=self.actor)
        if comment_id is not None:
            return StudentVideoCommentDetailView.as_view()(request, comment_id=comment_id)
        return StudentVideoCommentListView.as_view()(request, video_id=video.id)

    def test_student_replay_keeps_completion_and_skip_usage_but_moves_resume_backward(self):
        first = self.fixture._post_progress({"progress": 0.95, "completed": True, "last_position": 95})
        self.assertEqual(first.status_code, 200)
        VideoProgress.objects.filter(pk=first.data["id"]).update(forward_skip_seconds_used=7)
        replay = self.fixture._post_progress({"progress": 0.10, "completed": False, "last_position": 10})
        self.assertEqual(replay.status_code, 200)
        self.assertEqual(replay.data["progress"], 0.95)
        self.assertTrue(replay.data["completed"])
        self.assertEqual(replay.data["last_position"], 10)
        stored = VideoProgress.objects.get(pk=first.data["id"])
        self.assertTrue(stored.completed)
        self.assertEqual(stored.forward_skip_seconds_used, 7)

    def test_threshold_completion_keeps_raw_false_and_review_after_stale_update(self):
        self.fixture._post_progress({"progress": 0.95, "completed": False})
        replay = self.fixture._post_progress({"progress": 0.1, "completed": False})
        self.assertEqual(replay.data["progress"], 0.95)
        self.assertTrue(replay.data["completed"])
        self.assertFalse(VideoProgress.objects.get(video=self.video, enrollment_id=self.enrollment_id).completed)

    def test_explicit_reset_remains_authoritative_for_later_student_updates(self):
        self.fixture._post_progress({"progress": 0.95, "completed": True})
        teacher = User.objects.create_user(
            username="video-reset-teacher", password="test-only", tenant=self.fixture.tenant, is_staff=True,
        )
        TenantMembership.ensure_active(tenant=self.fixture.tenant, user=teacher, role="teacher")
        progress = VideoProgress.objects.get(video=self.video, enrollment_id=self.enrollment_id)
        request = self.factory.patch("/media/video-progress/", {
            "progress": 0, "completed": False, "last_position": 0,
        }, format="json")
        request.tenant = self.fixture.tenant
        force_authenticate(request, user=teacher)
        reset = VideoProgressViewSet.as_view({"patch": "partial_update"})(request, pk=progress.pk)
        self.assertEqual(reset.status_code, 200, reset.data)
        response = self.fixture._post_progress({"progress": 0.1, "completed": False, "last_position": 10})
        self.assertEqual(response.data["progress"], 0.1)
        self.assertFalse(response.data["completed"])

    def test_inactive_progress_merges_only_after_locked_entitlement_revalidation(self):
        self.fixture._post_progress({"progress": 0.95, "completed": True, "last_position": 95})
        enrollment = VideoProgress.objects.get(video=self.video, enrollment_id=self.enrollment_id).enrollment
        with patch("apps.domains.video.services.inactive_entitlements.lock_and_revalidate_inactive_video_write_access", return_value=SimpleNamespace(video=self.video, enrollment=enrollment)) as validate:
            progress, created = update_inactive_entitled_video_progress(
                tenant_id=self.video.tenant_id, enrollment_id=enrollment.id, video_id=self.video.id,
                expected_policy_version=self.video.policy_version or 1,
                defaults={"progress": 0.1, "completed": False, "last_position": 10},
            )
        validate.assert_called_once_with(tenant_id=self.video.tenant_id, enrollment_id=enrollment.id, video_id=self.video.id, expected_policy_version=self.video.policy_version or 1)
        self.assertFalse(created)
        self.assertEqual(progress.progress, 0.95)
        self.assertTrue(progress.completed)
        self.assertEqual(progress.last_position, 10)

    def test_single_and_effective_resolvers_agree_for_offline_supervised_override(self):
        enrollment = VideoProgress.objects.get(video=self.video, enrollment_id=self.enrollment_id).enrollment
        VideoAccess.objects.update_or_create(video=self.video, enrollment=enrollment, defaults={"access_mode": AccessMode.PROCTORED_CLASS, "is_override": True})
        self.assertEqual(resolve_access_mode(video=self.video, enrollment=enrollment), AccessMode.PROCTORED_CLASS)
        self.assertEqual(get_effective_access_mode(video=self.video, enrollment=enrollment), AccessMode.PROCTORED_CLASS)
        self.fixture._post_progress({"progress": 0.95, "completed": False})
        self.assertEqual(resolve_access_mode(video=self.video, enrollment=enrollment), AccessMode.FREE_REVIEW)
        self.assertEqual(get_effective_access_mode(video=self.video, enrollment=enrollment), AccessMode.FREE_REVIEW)

    def test_deleted_root_retains_existing_authored_reply(self):
        reply = self._comment_request("post", {"parent_id": self.root_comment.id, "content": "authored reply"})
        self.assertEqual(reply.status_code, 201, reply.data)
        deleted = self._comment_request("delete", comment_id=self.root_comment.id)
        self.assertEqual(deleted.status_code, 200)
        response = self._comment_request("get")
        root = next(row for row in response.data["comments"] if row["id"] == self.root_comment.id)
        self.assertTrue(root["is_deleted"])
        self.assertEqual(root["content"], "")
        self.assertEqual(root["reply_count"], 1)
        self.assertEqual(root["replies"][0]["content"], "authored reply")
        self.root_comment.refresh_from_db()
        self.assertTrue(self.root_comment.content)
        self.assertFalse(VideoComment.objects.get(pk=reply.data["id"]).is_deleted)

    def test_deleted_root_rejects_new_reply_without_row_or_counter_changes(self):
        self._comment_request("delete", comment_id=self.root_comment.id)
        before = VideoComment.objects.count()
        self.video.refresh_from_db()
        before_count = self.video.comment_count
        response = self._comment_request("post", {"parent_id": self.root_comment.id, "content": "late reply"})
        self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(VideoComment.objects.count(), before)
        self.video.refresh_from_db()
        self.assertEqual(self.video.comment_count, before_count)

    def test_normal_reply_persists_and_non_root_parent_is_rejected(self):
        reply = self._comment_request("post", {"parent_id": self.root_comment.id, "content": "normal reply"})
        self.assertEqual(reply.status_code, 201)
        response = self._comment_request("post", {"parent_id": reply.data["id"], "content": "nested reply"})
        self.assertEqual(response.status_code, 400)
        response = self._comment_request("get")
        root = next(row for row in response.data["comments"] if row["id"] == self.root_comment.id)
        self.assertEqual(root["replies"][0]["id"], reply.data["id"])


    def test_playlist_and_playback_access_check_agree_for_overrides(self):
        enrollment = self.fixture.target_enrollment
        for attendance, override in (("PRESENT", AccessMode.PROCTORED_CLASS), ("ONLINE", AccessMode.FREE_REVIEW)):
            with self.subTest(attendance=attendance, override=override):
                Attendance.objects.update_or_create(
                    session=self.fixture.target_session, enrollment=enrollment,
                    defaults={"status": attendance, "tenant": self.fixture.tenant},
                )
                VideoAccess.objects.update_or_create(video=self.video, enrollment=enrollment, defaults={
                    "access_mode": override, "is_override": True,
                })
                listed = self.fixture._get_session_videos()
                checked = self.fixture._get_playback(access_check=True)
                self.assertEqual(listed.status_code, 200, listed.data)
                self.assertEqual(checked.status_code, 200, checked.data)
                items = listed.data if isinstance(listed.data, list) else listed.data["items"]
                item = next(row for row in items if row["id"] == self.video.id)
                self.assertEqual(item["access_mode"], override.value)
                self.assertEqual(checked.data["access_mode"], override.value)

    def test_batched_overrides_do_not_query_per_video(self):
        permission = SimpleNamespace(access_mode=AccessMode.PROCTORED_CLASS, is_override=True, rule="allowed", proctored_completed_at=None)
        videos = [SimpleNamespace(id=index) for index in range(30)]
        with self.assertNumQueries(0):
            result = resolve_access_modes_for_videos_prefetched(
                videos=videos, enrollment=self.fixture.target_enrollment,
                progresses_by_video_id={}, access_by_video_id={video.id: permission for video in videos},
                attendance_status="PRESENT",
            )
        self.assertEqual(set(result.values()), {AccessMode.PROCTORED_CLASS})
        self.assertEqual(len(result), 30)

    def test_completion_flag_survives_partial_stale_update(self):
        self.fixture._post_progress({"progress": 0.5, "completed": True, "last_position": 50})
        result = self.fixture._post_progress({"completed": False, "last_position": 5})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.data["progress"], 0.5)
        self.assertTrue(result.data["completed"])
        self.assertEqual(result.data["last_position"], 5)

    def test_deleted_reply_stays_hidden_and_repeat_delete_does_not_change_count(self):
        reply = self._comment_request("post", {"parent_id": self.root_comment.id, "content": "retained but hidden"})
        self.assertEqual(reply.status_code, 201)
        self._comment_request("delete", comment_id=reply.data["id"])
        self._comment_request("delete", comment_id=self.root_comment.id)
        self._comment_request("delete", comment_id=self.root_comment.id)
        self.video.refresh_from_db()
        self.assertEqual(self.video.comment_count, 0)
        root = next(row for row in self._comment_request("get").data["comments"] if row["id"] == self.root_comment.id)
        self.assertEqual(root["replies"], [])
        self.assertEqual(root["reply_count"], 0)
        self.assertEqual(VideoComment.objects.get(pk=reply.data["id"]).content, "retained but hidden")

    def test_reply_parent_must_match_video_and_tenant(self):
        other_video = Video.objects.create(
            tenant=self.fixture.tenant, session=self.fixture.target_session,
            title="Other test video", status=Video.Status.READY, order=2,
        )
        before = VideoComment.objects.count()
        response = self._comment_request("post", {"parent_id": self.root_comment.id, "content": "wrong video"}, video=other_video)
        self.assertEqual(response.status_code, 400, response.data)
        other_tenant = Tenant.objects.create(code="video-reply-other", name="Other tenant")
        foreign_parent = VideoComment.objects.create(
            tenant=other_tenant, video=self.video, content="foreign authored content",
        )
        response = self._comment_request("post", {"parent_id": foreign_parent.id, "content": "wrong tenant"})
        self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(VideoComment.objects.count(), before + 1)
        listed = self._comment_request("get")
        self.assertNotIn(foreign_parent.id, [row["id"] for row in listed.data["comments"]])

    def test_comment_creation_and_counter_roll_back_together(self):
        from django.db.models.query import QuerySet
        original_update = QuerySet.update
        def failing_video_counter(queryset, **kwargs):
            if queryset.model is Video and "comment_count" in kwargs:
                raise RuntimeError("test counter failure")
            return original_update(queryset, **kwargs)
        before = VideoComment.objects.count()
        self.video.refresh_from_db()
        before_count = self.video.comment_count
        with patch.object(QuerySet, "update", failing_video_counter):
            with self.assertRaisesRegex(RuntimeError, "test counter failure"):
                self._comment_request("post", {"parent_id": self.root_comment.id, "content": "must roll back"})
        self.assertEqual(VideoComment.objects.count(), before)
        self.video.refresh_from_db()
        self.assertEqual(self.video.comment_count, before_count)


@skipUnless(connection.vendor == "postgresql", "Requires PostgreSQL row locks; mandatory PostgreSQL CI executes this case")
@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class StudentVideoConcurrentProgressTests(TransactionTestCase):
    def setUp(self):
        self.fixture = fixtures.StudentVideoProgressEnrollmentResolutionTests(methodName="runTest")
        self.fixture.setUp()
        self.video = Video.objects.get(session=self.fixture.target_session)
        self.enrollment = self.fixture.target_enrollment

    def test_concurrent_first_and_existing_writes_keep_the_highest_completion(self):
        from apps.domains.video.services.student_progress import update_student_video_progress

        for existing in (False, True):
            with self.subTest(existing=existing):
                VideoProgress.objects.filter(video=self.video, enrollment=self.enrollment).delete()
                if existing:
                    VideoProgress.objects.create(video=self.video, enrollment=self.enrollment, progress=0.2)
                barrier = Barrier(2)

                def save(values):
                    close_old_connections()
                    try:
                        barrier.wait(timeout=10)
                        update_student_video_progress(video=self.video, enrollment=self.enrollment, defaults=values)
                    finally:
                        connections.close_all()

                with ThreadPoolExecutor(max_workers=2) as pool:
                    tasks = [pool.submit(save, values) for values in (
                        {"progress": 0.95, "completed": True, "last_position": 95},
                        {"progress": 0.1, "completed": False, "last_position": 10},
                    )]
                    for task in tasks:
                        task.result(timeout=20)
                rows = VideoProgress.objects.filter(video=self.video, enrollment=self.enrollment)
                self.assertEqual(rows.count(), 1)
                result = rows.get()
                self.assertEqual(result.progress, 0.95)
                self.assertTrue(result.completed)
                self.assertIn(result.last_position, (10, 95))
