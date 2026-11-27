from datetime import date, time, timedelta
from unittest.mock import patch

from django.utils import timezone
from rest_framework.exceptions import ValidationError
from rest_framework.test import APITestCase

from apps.core.models import Tenant, TenantMembership, User
from apps.domains.attendance.models import Attendance
from apps.domains.attendance.services import ensure_session_roster_membership
from apps.domains.enrollment.models import Enrollment, SessionEnrollment
from apps.domains.lectures.models import Lecture, Section, Session
from apps.domains.parents.models import Parent
from apps.domains.students.models import Student
from apps.domains.video.models import AccessMode, InactiveVideoEntitlement, Video, VideoAccess, VideoProgress


class SessionWithdrawalVideoAccessTests(APITestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(code="qa-withdrawal", name="QA", is_active=True)
        self.staff = User.objects.create_user(username="qa-withdrawal-admin", tenant=self.tenant, is_staff=True)
        TenantMembership.ensure_active(tenant=self.tenant, user=self.staff, role="admin")
        self.user = User.objects.create_user(username="qa-withdrawal-student", tenant=self.tenant)
        TenantMembership.ensure_active(tenant=self.tenant, user=self.user, role="student")
        self.student = Student.objects.create(
            tenant=self.tenant, user=self.user, name="QA", ps_number="QAW1",
            omr_code="87654321", parent_phone="01000000000",
        )
        lecture = Lecture.objects.create(tenant=self.tenant, name="QA", title="QA", subject="MATH")
        self.enrollment = Enrollment.objects.create(tenant=self.tenant, student=self.student, lecture=lecture)
        self.videos = []
        self.attendances = []
        for order in range(1, 9):
            session = Session.objects.create(lecture=lecture, title=f"Session {order}", order=order)
            SessionEnrollment.objects.create(tenant=self.tenant, session=session, enrollment=self.enrollment)
            self.attendances.append(Attendance.objects.create(
                tenant=self.tenant, session=session, enrollment=self.enrollment, status="ONLINE",
            ))
            self.videos.append(Video.objects.create(
                tenant=self.tenant, session=session, title=f"Video {order}", duration=600, status=Video.Status.READY,
            ))
        self.headers = {"HTTP_HOST": "localhost", "HTTP_X_TENANT_CODE": self.tenant.code}

    def access(self, index):
        self.client.force_authenticate(self.user)
        return self.client.get(
            f"/api/v1/student/video/videos/{self.videos[index].id}/playback/?access_check=true", **self.headers,
        )

    def withdraw(self, scope):
        self.client.force_authenticate(self.staff)
        return self.client.patch(
            f"/api/v1/lectures/attendance/{self.attendances[2].id}/",
            {"status": "SECESSION", "confirm_secession": True, "secession_scope": scope},
            format="json", **self.headers,
        )

    def reregister(self, *, enrollment_api=False):
        self.client.force_authenticate(self.staff)
        url = (
            "/api/v1/enrollments/session-enrollments/bulk_create/"
            if enrollment_api else "/api/v1/lectures/attendance/bulk_create/"
        )
        payload = {"session": self.attendances[2].session_id}
        payload["enrollments" if enrollment_api else "students"] = [
            self.enrollment.id if enrollment_api else self.student.id
        ]
        return self.client.post(url, payload, format="json", **self.headers)

    def test_session_withdrawal_revokes_monitored_token_preserves_other_seven_and_history(self):
        self.client.force_authenticate(self.user)
        with patch("apps.domains.video.services.playback_session.init_session_redis"):
            start = self.client.post(
                f"/api/v1/student/video/videos/{self.videos[2].id}/playback/",
                {"device_id": "qa-withdrawal-device"}, format="json", **self.headers,
            )
        self.assertEqual(start.status_code, 200)
        self.assertEqual(start.data["policy"]["access_mode"], "PROCTORED_CLASS")
        progress = VideoProgress.objects.create(
            video=self.videos[2], enrollment=self.enrollment, progress=0.25, last_position=150,
        )
        response = self.withdraw("session")
        self.assertEqual(response.status_code, 200)
        self.enrollment.refresh_from_db()
        progress.refresh_from_db()
        self.assertEqual(self.enrollment.status, "ACTIVE")
        self.assertEqual(progress.last_position, 150)
        for index in range(8):
            self.assertEqual(self.access(index).status_code, 403 if index == 2 else 200)
        self.client.force_authenticate(self.user)
        for action in ("refresh", "renew", "heartbeat"):
            result = self.client.post(
                f"/api/v1/media/playback/{action}/", {"token": start.data["playback_token"]},
                format="json", **self.headers,
            )
            self.assertEqual(result.status_code, 403, action)
        # Choosing whole-lecture withdrawal after session withdrawal still works.
        self.assertEqual(self.withdraw("lecture").status_code, 200)
        for index in range(8):
            self.assertEqual(self.access(index).status_code, 403)

    def test_explicit_lecture_withdrawal_revokes_all_eight(self):
        self.assertEqual(self.withdraw("lecture").status_code, 200)
        self.enrollment.refresh_from_db()
        self.assertEqual(self.enrollment.status, "INACTIVE")
        for index in range(8):
            self.assertEqual(self.access(index).status_code, 403)

    def lecture_attendances(self):
        return Attendance.objects.filter(tenant=self.tenant, enrollment=self.enrollment).order_by("session_id")

    def lecture_memberships(self):
        return SessionEnrollment.objects.filter(tenant=self.tenant, enrollment=self.enrollment).order_by("session_id")

    def prepare_inactive_roster(self):
        self.enrollment.status = "INACTIVE"
        self.enrollment.save(update_fields=["status"])
        self.lecture_attendances().filter(pk__in=[row.pk for row in self.attendances[:2]]).delete()
        self.lecture_attendances().update(status="SECESSION")

    def reenroll_in_lecture(self):
        self.client.force_authenticate(self.staff)
        result = self.client.post(
            "/api/v1/enrollments/bulk_create/",
            {"lecture": self.enrollment.lecture_id, "students": [self.student.pk]}, format="json", **self.headers,
        )
        self.assertEqual(result.status_code, 201, result.data)
        self.assertEqual([(row["id"], row["status"]) for row in result.data], [(self.enrollment.pk, "ACTIVE")])

    def test_whole_lecture_withdrawal_records_missing_attendance_only_for_its_roster(self):
        self.lecture_attendances().filter(pk__in=[row.pk for row in self.attendances[:2]]).delete()
        roster = list(self.lecture_memberships().values_list("session_id", "pk"))
        # Regular active roster membership predates mandatory attendance creation.
        self.assertEqual(self.access(0).status_code, 200)
        self.assertFalse(self.lecture_attendances().filter(session_id=self.videos[0].session_id).exists())
        unassigned = Session.objects.create(lecture=self.enrollment.lecture, title="Unassigned", order=9)
        other_tenant = Tenant.objects.create(code="qa-roster-control", name="QA control")
        foreign = Attendance.objects.create(
            tenant=other_tenant, enrollment=self.enrollment, session_id=self.videos[0].session_id,
            status="PRESENT", memo="Keep foreign record",
        )
        foreign_membership = SessionEnrollment.objects.create(
            tenant=other_tenant, enrollment=self.enrollment, session=unassigned,
        )

        self.assertEqual(self.withdraw("lecture").status_code, 200)
        recorded = list(self.lecture_attendances().values_list("session_id", "pk", "status"))
        self.assertEqual([(sid, status) for sid, _, status in recorded], [(sid, "SECESSION") for sid, _ in roster])
        self.assertEqual([pk for _, pk, _ in recorded[2:]], [row.pk for row in self.attendances[2:]])
        self.assertEqual(self.withdraw("lecture").status_code, 200)
        self.assertEqual(list(self.lecture_attendances().values_list("session_id", "pk", "status")), recorded)
        self.assertEqual(list(self.lecture_memberships().values_list("session_id", "pk")), roster)
        foreign.refresh_from_db()
        self.assertEqual((foreign.status, foreign.memo), ("PRESENT", "Keep foreign record"))
        self.assertTrue(SessionEnrollment.objects.filter(pk=foreign_membership.pk, tenant=other_tenant).exists())
        self.assertFalse(Attendance.objects.filter(session=unassigned).exists())
        self.enrollment.refresh_from_db()
        self.assertEqual(self.enrollment.status, "INACTIVE")

    def test_historical_whole_lecture_restore_opens_only_selected_session_even_when_its_membership_is_missing(self):
        self.prepare_inactive_roster()
        target = self.attendances[2]
        target.memo = "Keep selected history"
        target.save(update_fields=["memo"])
        self.lecture_memberships().filter(session_id=target.session_id).delete()
        retained = list(self.lecture_memberships().values_list("session_id", "pk"))
        self.client.force_authenticate(self.staff)
        for _ in range(2):
            result = self.client.patch(
                f"/api/v1/lectures/attendance/{target.pk}/", {"status": "ONLINE", "restore_scope": "lecture"},
                format="json", **self.headers,
            )
            self.assertEqual(result.status_code, 200, result.data)
            self.assertEqual((result.data["id"], result.data["status"], result.data["memo"]),
                             (target.pk, "ONLINE", "Keep selected history"))
        self.enrollment.refresh_from_db()
        self.assertEqual(self.enrollment.status, "ACTIVE")
        memberships = self.lecture_memberships()
        self.assertEqual(memberships.filter(session_id=target.session_id).count(), 1)
        self.assertEqual(list(memberships.exclude(session_id=target.session_id).values_list("session_id", "pk")), retained)
        self.assertEqual(list(self.lecture_attendances().values_list("status", flat=True)),
                         ["SECESSION", "SECESSION", "ONLINE"] + ["SECESSION"] * 5)
        self.assertEqual([self.access(i).status_code for i in (0, 1, 2)], [403, 403, 200])

    def test_cancelled_or_failed_lecture_restore_leaves_missing_history_untouched(self):
        self.prepare_inactive_roster()
        target = self.attendances[2]
        initial = list(self.lecture_attendances().values_list("pk", "status"))
        self.client.force_authenticate(self.staff)
        path = f"/api/v1/lectures/attendance/{target.pk}/"
        response = self.client.patch(path, {"status": "ONLINE", "restore_scope": "session"},
                                     format="json", **self.headers)
        self.assertEqual(response.status_code, 409)
        with patch("apps.domains.attendance.views.reconcile_learning_todo_targets",
                   side_effect=ValidationError("test rollback")):
            response = self.client.patch(path, {"status": "ONLINE", "restore_scope": "lecture"},
                                         format="json", **self.headers)
        self.assertEqual(response.status_code, 400)
        self.enrollment.refresh_from_db()
        self.assertEqual(self.enrollment.status, "INACTIVE")
        self.assertEqual(list(self.lecture_attendances().values_list("pk", "status")), initial)

    def test_ordinary_reenrollment_preserves_whole_lecture_withdrawal_without_selecting_a_session(self):
        self.prepare_inactive_roster()
        roster = list(self.lecture_memberships().values_list("session_id", "pk"))
        self.reenroll_in_lecture()
        recorded = list(self.lecture_attendances().values_list("session_id", "pk", "status"))
        self.assertEqual([(sid, status) for sid, _, status in recorded], [(sid, "SECESSION") for sid, _ in roster])
        self.reenroll_in_lecture()
        self.assertEqual(list(self.lecture_attendances().values_list("session_id", "pk", "status")), recorded)
        self.assertEqual(list(self.lecture_memberships().values_list("session_id", "pk")), roster)
        self.assertEqual([self.access(i).status_code for i in (0, 1, 2)], [403, 403, 403])

    def test_ordinary_reenrollment_preserves_unrecorded_roster_when_only_one_session_was_withdrawn(self):
        self.prepare_inactive_roster()
        self.lecture_attendances().exclude(pk=self.attendances[2].pk).update(status="PRESENT")
        history = list(self.lecture_attendances().values_list("session_id", "pk", "status"))
        roster = list(self.lecture_memberships().values_list("session_id", "pk"))
        self.reenroll_in_lecture()
        self.assertEqual(list(self.lecture_attendances().values_list("session_id", "pk", "status")), history)
        self.assertEqual(list(self.lecture_memberships().values_list("session_id", "pk")), roster)
        self.assertEqual([self.access(i).status_code for i in (0, 1, 2)], [200, 200, 403])

    def test_course_status_edit_keeps_full_withdrawal_across_pending_then_active(self):
        self.prepare_inactive_roster()
        self.client.force_authenticate(self.staff)
        for state in ("PENDING", "ACTIVE"):
            response = self.client.patch(f"/api/v1/enrollments/{self.enrollment.pk}/", {"status": state},
                                         format="json", **self.headers)
            self.assertEqual(response.status_code, 200, response.data)
            self.assertEqual(response.data["status"], state)
            self.assertEqual(list(self.lecture_attendances().values_list("status", flat=True)), ["SECESSION"] * 8)
        self.assertEqual(self.access(0).status_code, 403)

    def test_pause_with_only_removed_session_history_does_not_imply_whole_withdrawal(self):
        self.prepare_inactive_roster()
        self.lecture_memberships().filter(session_id__in=[row.session_id for row in self.attendances[2:]]).delete()
        initial = list(self.lecture_attendances().values_list("pk", "status"))
        self.reenroll_in_lecture()
        self.assertEqual(list(self.lecture_attendances().values_list("pk", "status")), initial)
        self.assertEqual([self.access(i).status_code for i in (0, 2)], [200, 403])

    def test_excel_reactivation_repairs_old_withdrawal_outside_the_selected_session(self):
        from apps.domains.enrollment.services import lecture_enroll_from_excel_rows

        self.prepare_inactive_roster()
        lecture_enroll_from_excel_rows(
            tenant_id=self.tenant.pk, lecture_id=self.enrollment.lecture_id,
            students_data=[{"name": self.student.name, "ps_number": self.student.ps_number}],
            session_id=self.attendances[2].session_id,
        )
        self.enrollment.refresh_from_db()
        self.assertEqual(self.enrollment.status, "ACTIVE")
        older = self.lecture_attendances().filter(session_id__in=[r.session_id for r in self.attendances[:2]])
        self.assertEqual(list(older.values_list("status", flat=True)), ["SECESSION", "SECESSION"])
        self.assertEqual([self.access(i).status_code for i in (0, 1)], [403, 403])

    def test_enrollment_with_no_attendance_history_remains_a_regular_roster(self):
        self.prepare_inactive_roster()
        self.lecture_attendances().delete()
        self.reenroll_in_lecture()
        self.assertFalse(self.lecture_attendances().exists())
        self.assertEqual(self.access(0).status_code, 200)

    def test_restoring_a_deleted_student_preserves_the_saved_course_state_and_attendance(self):
        from apps.domains.enrollment.services.lifecycle import restore_enrollments_after_student_restore

        self.prepare_inactive_roster()
        self.enrollment.status_before_student_deletion = "ACTIVE"
        self.enrollment.save(update_fields=["status_before_student_deletion"])
        before = list(self.lecture_attendances().values_list("pk", "status"))
        restore_enrollments_after_student_restore(tenant=self.tenant, student=self.student)
        self.enrollment.refresh_from_db()
        self.assertEqual(self.enrollment.status, "ACTIVE")
        self.assertEqual(list(self.lecture_attendances().values_list("pk", "status")), before)
        self.assertEqual(self.access(0).status_code, 200)

    def learner_requests(self):
        parent_user = User.objects.create_user(username="qa-withdrawal-parent", tenant=self.tenant)
        TenantMembership.ensure_active(tenant=self.tenant, user=parent_user, role="parent")
        self.student.parent = Parent.objects.create(
            tenant=self.tenant, user=parent_user, name="QA parent", phone=self.student.parent_phone,
        )
        self.student.save(update_fields=["parent"])
        return [(self.user, self.headers), (parent_user, {**self.headers, "HTTP_X_STUDENT_ID": str(self.student.pk)})]

    def restore_third_session(self):
        self.assertEqual(self.withdraw("lecture").status_code, 200)
        target = self.attendances[2]
        response = self.client.patch(
            f"/api/v1/lectures/attendance/{target.pk}/",
            {"status": "ONLINE", "restore_scope": "lecture"}, format="json", **self.headers,
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.enrollment.refresh_from_db()
        self.assertEqual(self.enrollment.status, "ACTIVE")
        self.assertEqual(SessionEnrollment.objects.filter(enrollment=self.enrollment).count(), 8)
        attendance = Attendance.objects.filter(enrollment=self.enrollment).order_by("session__order")
        self.assertEqual(list(attendance.values_list("status", flat=True)),
                         ["SECESSION", "SECESSION", "ONLINE"] + ["SECESSION"] * 5)

    @patch("apps.domains.video.services.playback_session.is_redis_available", return_value=False)
    @patch("apps.domains.video.services.playback_session.init_session_redis")
    def test_lecture_restore_limits_student_and_parent_video_catalog_and_tokens_to_restored_session(self, _init, _redis):
        roles = self.learner_requests()
        old_tokens = {}
        first, second, restored = self.videos[:3]
        # This playback setting must not independently grant access to a withdrawn session.
        VideoAccess.objects.create(video=first, enrollment=self.enrollment, access_mode=AccessMode.FREE_REVIEW, is_override=True)

        def bootstrap(video, headers):
            return self.client.post(f"/api/v1/student/video/videos/{video.pk}/playback/",
                                    {}, format="json", **headers)

        def refresh(token, headers):
            return self.client.post("/api/v1/media/playback/refresh/", {"token": token},
                                    format="json", **headers)

        for user, headers in roles:
            self.client.force_authenticate(user)
            response = bootstrap(first, headers)
            self.assertEqual(response.status_code, 200, response.data)
            self.assertEqual(response.data["policy"]["access_mode"], "FREE_REVIEW")
            old_tokens[user.pk] = response.data["playback_token"]
            self.assertEqual(refresh(old_tokens[user.pk], headers).status_code, 200)

        self.restore_third_session()
        for user, headers in roles:
            self.client.force_authenticate(user)
            with self.subTest(user=user.username, surface="catalog"):
                home = self.client.get("/api/v1/student/video/me/", **headers)
                self.assertEqual(home.status_code, 200)
                course, = home.data["lectures"]
                self.assertEqual(course["id"], self.enrollment.lecture_id)
                self.assertEqual((course["video_count"], [row["id"] for row in course["sessions"]]),
                                 (1, [restored.session_id]))
                stats = self.client.get("/api/v1/student/video/me/stats/", **headers)
                self.assertEqual(stats.status_code, 200)
                self.assertEqual(stats.data["total_videos"], 1)
            for video in (first, second, restored):
                expected = 200 if video == restored else 403
                with self.subTest(user=user.username, session=video.session.order):
                    playlist = self.client.get(f"/api/v1/student/video/sessions/{video.session_id}/videos/", **headers)
                    self.assertEqual(playlist.status_code, expected, playlist.data)
                    check = self.client.get(f"/api/v1/student/video/videos/{video.pk}/playback/",
                                            {"access_check": "true"}, **headers)
                    self.assertEqual(check.status_code, expected, check.data)
                    opened = bootstrap(video, headers)
                    self.assertEqual(opened.status_code, expected, opened.data)
                    if video == restored:
                        self.assertEqual([row["id"] for row in playlist.data["items"]], [video.pk])
                        self.assertEqual(opened.data["policy"]["access_mode"], "PROCTORED_CLASS")
                        self.assertEqual(refresh(opened.data["playback_token"], headers).status_code, 200)
            with self.subTest(user=user.username, surface="previous token"):
                self.assertEqual(refresh(old_tokens[user.pk], headers).status_code, 403)

    def test_lecture_restore_limits_schedules_and_details_but_keeps_withdrawn_attendance_history(self):
        roles = self.learner_requests()
        Session.objects.filter(lecture=self.enrollment.lecture).update(date=timezone.localdate())
        self.restore_third_session()
        first, second, restored = [row.session_id for row in self.attendances[:3]]
        staff = self.client.get("/api/v1/lectures/attendance/", {"session": first}, **self.headers)
        self.assertEqual(staff.status_code, 200, staff.data)
        self.assertEqual([(row["id"], row["status"]) for row in staff.data["results"]],
                         [(self.attendances[0].pk, "SECESSION")])
        for user, headers in roles:
            self.client.force_authenticate(user)
            for path, key in (("sessions/me/", None), ("sessions/me/?scope=home", "items"), ("dashboard/", "today_sessions")):
                with self.subTest(user=user.username, path=path):
                    response = self.client.get("/api/v1/student/" + path, **headers)
                    self.assertEqual(response.status_code, 200)
                    items = response.data if key is None else response.data[key]
                    self.assertEqual([row["id"] for row in items], [restored])
            for session_id in (first, second, restored):
                with self.subTest(user=user.username, session=session_id):
                    detail = self.client.get(f"/api/v1/student/sessions/{session_id}/", **headers)
                    self.assertEqual(detail.status_code, 200 if session_id == restored else 404, detail.data)
            with self.subTest(user=user.username, surface="attendance history"):
                summary = self.client.get("/api/v1/student/attendance/summary/", **headers)
                self.assertEqual(summary.status_code, 200)
                history = {row["session_id"]: (row["status"], row["can_view_session"]) for row in summary.data["recent"]}
                self.assertEqual([history[sid] for sid in (first, second, restored)],
                                 [("SECESSION", False), ("SECESSION", False), ("ONLINE", True)])

    def test_withdrawn_sessions_preserve_exact_inactive_video_grant_and_public_video(self):
        self.assertEqual(self.withdraw("lecture").status_code, 200)
        granted, public = self.videos[:2]
        public.visibility = Video.Visibility.PUBLIC
        public.save(update_fields=["visibility"])
        sibling = Video.objects.create(tenant=self.tenant, session=granted.session, title="Private sibling",
                                       status=Video.Status.READY, order=2)
        grant = self.client.post("/api/v1/media/inactive-video-entitlements/", {
            "student_id": self.student.id, "enrollment_id": self.enrollment.id, "video_id": granted.id,
            "access_mode": AccessMode.FREE_REVIEW, "source": InactiveVideoEntitlement.Source.STAFF_AUTHORIZATION,
            "source_reference": "qa:retained-membership", "reason": "Exact withdrawn video",
            "expires_at": (timezone.now() + timedelta(days=1)).isoformat(),
        }, format="json", **self.headers)
        self.assertEqual(grant.status_code, 201, grant.data)
        self.client.force_authenticate(self.user)
        playlist = self.client.get(f"/api/v1/student/video/sessions/{granted.session_id}/videos/", **self.headers)
        self.assertEqual(playlist.status_code, 200, playlist.data)
        self.assertEqual([row["id"] for row in playlist.data["items"]], [granted.pk])
        for video, expected in ((granted, 200), (sibling, 403), (public, 200)):
            with self.subTest(video=video.title):
                access = self.client.get(f"/api/v1/student/video/videos/{video.pk}/playback/",
                                         {"access_check": "true"}, **self.headers)
                self.assertEqual(access.status_code, expected, access.data)

    def test_explicit_session_reregistration_restores_editable_attendance_and_preserves_history(self):
        # Exercise the registration endpoint used by the attendance roster UI.
        Attendance.objects.filter(tenant=self.tenant, enrollment=self.enrollment).delete()
        SessionEnrollment.objects.filter(tenant=self.tenant, enrollment=self.enrollment).delete()
        self.attendances = []
        for video in self.videos:
            self.client.force_authenticate(self.staff)
            registered = self.client.post(
                "/api/v1/lectures/attendance/bulk_create/",
                {"session": video.session_id, "students": [self.student.id]},
                format="json", **self.headers,
            )
            self.assertEqual(registered.status_code, 201, registered.data)
            attendance = Attendance.objects.get(pk=registered.data[0]["id"])
            updated = self.client.patch(
                f"/api/v1/lectures/attendance/{attendance.id}/",
                {"status": "ONLINE", "memo": f"Keep session {video.session.order} history"},
                format="json", **self.headers,
            )
            self.assertEqual(updated.status_code, 200, updated.data)
            attendance.refresh_from_db()
            self.attendances.append(attendance)

        original_rows = [(row.id, row.status, row.memo) for row in self.attendances]
        target = self.attendances[2]
        progress = VideoProgress.objects.create(
            video=self.videos[2], enrollment=self.enrollment, progress=0.25, last_position=150,
        )
        for index in range(8):
            self.assertEqual(self.access(index).status_code, 200)

        self.assertEqual(self.withdraw("session").status_code, 200)
        self.assertEqual(self.access(2).status_code, 403)
        self.client.force_authenticate(self.staff)
        ordinary_update = self.client.patch(
            f"/api/v1/lectures/attendance/{target.id}/", {"status": "PRESENT"},
            format="json", **self.headers,
        )
        self.assertEqual(ordinary_update.status_code, 409, ordinary_update.data)
        self.assertFalse(SessionEnrollment.objects.filter(
            tenant=self.tenant, enrollment=self.enrollment, session_id=target.session_id,
        ).exists())

        registered_again = self.client.post(
            "/api/v1/lectures/attendance/bulk_create/",
            {"session": target.session_id, "students": [self.student.id]},
            format="json", **self.headers,
        )
        self.assertEqual(registered_again.status_code, 201, registered_again.data)
        self.assertEqual(registered_again.data[0]["id"], target.id)
        self.assertEqual(SessionEnrollment.objects.filter(
            tenant=self.tenant, enrollment=self.enrollment, session_id=target.session_id,
        ).count(), 1)
        reloaded = self.client.get(f"/api/v1/lectures/attendance/{target.id}/", **self.headers)
        self.assertEqual(reloaded.status_code, 200, reloaded.data)
        with self.subTest(step="explicit reregistration clears the terminal attendance state"):
            self.assertEqual(reloaded.data["status"], "UNSET")

        # A successful re-registration must restore normal editing, not only video access.
        for attendance_status in ("PRESENT", "ONLINE"):
            updated = self.client.patch(
                f"/api/v1/lectures/attendance/{target.id}/", {"status": attendance_status},
                format="json", **self.headers,
            )
            with self.subTest(step="normal attendance editing", status=attendance_status):
                self.assertEqual(updated.status_code, 200, updated.data)
        reloaded = self.client.get(f"/api/v1/lectures/attendance/{target.id}/", **self.headers)
        with self.subTest(step="edited attendance persists after reload"):
            self.assertEqual(reloaded.data["status"], "ONLINE")
        self.assertEqual(reloaded.data["memo"], original_rows[2][2])
        self.enrollment.refresh_from_db()
        progress.refresh_from_db()
        self.assertEqual(self.enrollment.status, "ACTIVE")
        self.assertEqual(progress.progress, 0.25)
        self.assertEqual(progress.last_position, 150)
        for index, attendance in enumerate(self.attendances):
            self.assertEqual(self.access(index).status_code, 200)
            if index != 2:
                attendance.refresh_from_db()
                self.assertEqual((attendance.id, attendance.status, attendance.memo), original_rows[index])

    def test_explicit_session_enrollment_repairs_retained_membership_and_repeats_preserve_edits(self):
        target = self.attendances[2]
        section = Section.objects.create(
            tenant=self.tenant, lecture=self.enrollment.lecture, label="A",
            day_of_week=0, start_time=time(17, 0),
        )
        target.memo = "Keep the original attendance record"
        target.planned_arrival_date = date(2026, 9, 21)
        target.planned_arrival_time = time(17, 30)
        target.attended_section = section
        target.save(update_fields=["memo", "planned_arrival_date", "planned_arrival_time", "attended_section"])
        recorded_at = target.recorded_at
        self.assertEqual(self.withdraw("session").status_code, 200)
        # Recover the historical partial re-registration state as well.
        membership = SessionEnrollment.objects.create(
            tenant=self.tenant, enrollment=self.enrollment, session=target.session,
        )
        registered = self.reregister(enrollment_api=True)
        self.assertEqual(registered.status_code, 201, registered.data)
        self.assertEqual(registered.data[0]["id"], membership.id)
        target.refresh_from_db()
        self.assertEqual(target.status, "UNSET")
        self.assertEqual(target.memo, "Keep the original attendance record")
        self.assertEqual(target.recorded_at, recorded_at)
        self.assertEqual(target.planned_arrival_date, date(2026, 9, 21))
        self.assertEqual(target.planned_arrival_time, time(17, 30))
        self.assertEqual(target.attended_section_id, section.id)
        updated = self.client.patch(
            f"/api/v1/lectures/attendance/{target.id}/", {"status": "PRESENT"},
            format="json", **self.headers,
        )
        self.assertEqual(updated.status_code, 200, updated.data)
        for enrollment_api in (True, False):
            repeated = self.reregister(enrollment_api=enrollment_api)
            self.assertEqual(repeated.status_code, 201, repeated.data)
            target.refresh_from_db()
            self.assertEqual(target.status, "PRESENT")
            self.assertEqual(target.memo, "Keep the original attendance record")
            self.assertEqual(target.recorded_at, recorded_at)
            self.assertEqual(target.planned_arrival_date, date(2026, 9, 21))
            self.assertEqual(target.planned_arrival_time, time(17, 30))
            self.assertEqual(target.attended_section_id, section.id)
        self.assertEqual(Attendance.objects.filter(
            tenant=self.tenant, enrollment=self.enrollment, session=target.session,
        ).count(), 1)
        self.assertEqual(SessionEnrollment.objects.get(
            tenant=self.tenant, enrollment=self.enrollment, session=target.session,
        ).id, membership.id)

    def test_reads_and_automatic_roster_calls_do_not_reregister_a_withdrawn_session(self):
        self.assertEqual(self.withdraw("session").status_code, 200)
        target = self.attendances[2]
        for url in ("/api/v1/lectures/attendance/", "/api/v1/enrollments/session-enrollments/"):
            response = self.client.get(url, {"session": target.session_id}, **self.headers)
            self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(self.access(2).status_code, 403)
        with patch("apps.domains.attendance.roster_membership.auto_assign_roster_fees") as assign_fees:
            with self.assertRaisesMessage(ValidationError, "명시적으로 재등록"):
                ensure_session_roster_membership(
                    tenant=self.tenant, enrollment=self.enrollment, session=target.session,
                )
        assign_fees.assert_not_called()
        target.refresh_from_db()
        self.assertEqual(target.status, "SECESSION")
        self.assertFalse(SessionEnrollment.objects.filter(
            tenant=self.tenant, enrollment=self.enrollment, session=target.session,
        ).exists())
        self.assertEqual(self.access(2).status_code, 403)

    def test_public_reregistration_cannot_reactivate_whole_lecture_withdrawal(self):
        self.assertEqual(self.withdraw("lecture").status_code, 200)
        for enrollment_api in (False, True):
            with self.subTest(enrollment_api=enrollment_api):
                registered = self.reregister(enrollment_api=enrollment_api)
                self.assertEqual(registered.status_code, 400, registered.data)
        self.enrollment.refresh_from_db()
        self.assertEqual(self.enrollment.status, "INACTIVE")
        self.assertFalse(Attendance.objects.filter(
            tenant=self.tenant, enrollment=self.enrollment,
        ).exclude(status="SECESSION").exists())
        for index in range(8):
            self.assertEqual(self.access(index).status_code, 403)
