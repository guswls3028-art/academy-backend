import csv
import io

from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.models import Tenant, TenantMembership, User
from apps.domains.enrollment.test_support import create_enrollment_fixture
from apps.domains.lectures.test_support import create_lecture_fixture, create_session_fixture
from apps.domains.students.test_support import create_student_fixture
from apps.domains.video.models import Video, VideoPlaybackEvent, VideoReviewWatchUsage
from apps.domains.video.views.event_views import VideoPlaybackEventViewSet
from apps.domains.video.views.review_watch_views import ReviewWatchReportView


class VideoWatchLogFilterTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.tenant = Tenant.objects.create(code="watch-logs", name="Watch logs")
        self.other = Tenant.objects.create(code="watch-logs-other", name="Other")
        self.staff = User.objects.create_user(username="watch-staff", tenant=self.tenant, is_staff=True)
        TenantMembership.ensure_active(tenant=self.tenant, user=self.staff, role="admin")
        self.student_user = User.objects.create_user(username="watch-student", tenant=self.tenant)
        TenantMembership.ensure_active(tenant=self.tenant, user=self.student_user, role="student")
        self.lecture = create_lecture_fixture(tenant=self.tenant, title="Video lecture", name="Video lecture", subject="MATH")
        self.session = create_session_fixture(lecture=self.lecture, title="Session", order=1)
        self.video = Video.objects.create(tenant=self.tenant, session=self.session, title="Watch log", duration=60)
        self.student = create_student_fixture(tenant=self.tenant, user=self.student_user, name="=학생", ps_number="WL-1", omr_code="11110001")
        self.second = create_student_fixture(tenant=self.tenant, user=User.objects.create_user(username="watch-second", tenant=self.tenant), name="Another", ps_number="WL-2", omr_code="11110002")
        self.foreign = create_student_fixture(tenant=self.other, user=User.objects.create_user(username="watch-foreign", tenant=self.other), name="Foreign", ps_number="WL-3", omr_code="11110003")
        self.enrollment = create_enrollment_fixture(tenant=self.tenant, lecture=self.lecture, student=self.student)
        other_enrollment = create_enrollment_fixture(tenant=self.tenant, lecture=self.lecture, student=self.second)
        # Deliberately inconsistent legacy relation must not leak another tenant.
        foreign_lecture = create_lecture_fixture(tenant=self.other, title="Foreign", name="Foreign", subject="MATH")
        foreign_enrollment = create_enrollment_fixture(tenant=self.other, lecture=foreign_lecture, student=self.foreign)
        self.event = self.event_for(self.enrollment)
        self.event_for(other_enrollment)
        self.event_for(foreign_enrollment)

    def event_for(self, enrollment):
        return VideoPlaybackEvent.objects.create(
            video=self.video, enrollment=enrollment, session_id=f"watch-{enrollment.pk}",
            user_id=self.staff.pk, event_type="SEEK_ATTEMPT", violated=True,
        )

    def request(self, action="list", user=None, **params):
        request = self.factory.get("/api/v1/media/video-playback-events/", {"video": self.video.pk, "range": "all", **params})
        request.tenant = self.tenant
        force_authenticate(request, user=user or self.staff)
        return VideoPlaybackEventViewSet.as_view({"get": action})(request)

    def test_student_identity_filters_list_risk_and_download(self):
        response = self.request(student=self.student.pk, page_size=1)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 1)
        self.assertEqual([row["id"] for row in response.data["results"]], [self.event.pk])
        risk = self.request("risk", student=self.student.pk)
        self.assertEqual([row["enrollment_id"] for row in risk.data], [self.enrollment.pk])
        exported = self.request("export_csv", student=self.student.pk)
        self.assertTrue(exported.content.startswith(b"\xef\xbb\xbf"))
        rows = list(csv.reader(io.StringIO(exported.content.decode("utf-8-sig"))))
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0][0], "occurred_at")
        self.assertEqual(rows[1][1], "'=학생")

    def test_foreign_student_and_malformed_identity_do_not_widen_query(self):
        self.assertEqual(self.request(student=self.foreign.pk).data["count"], 0)
        self.assertEqual(self.request().data["count"], 2)
        for invalid in ("0", "-1", "oops", "1.5"):
            with self.subTest(invalid=invalid):
                self.assertEqual(self.request(student=invalid).status_code, 400)
                self.assertEqual(self.request("export_csv", student=invalid).status_code, 400)

    def test_student_cannot_read_staff_logs_or_download(self):
        for action in ("list", "risk", "export_csv"):
            self.assertEqual(self.request(action, user=self.student_user).status_code, 403)

    def test_report_preserves_unlimited_and_direct_student_usage(self):
        direct = create_student_fixture(tenant=self.tenant, user=User.objects.create_user(username="watch-direct", tenant=self.tenant), name="Direct", ps_number="WL-4", omr_code="11110004")
        VideoReviewWatchUsage.objects.create(video=self.video, student=direct, used_ms=120000)
        VideoReviewWatchUsage.objects.create(video=self.video, student=self.student, used_ms=60000)
        VideoReviewWatchUsage.objects.create(video=self.video, student=self.foreign, used_ms=999999)
        self.video.review_watch_limit_enabled = False
        self.video.save(update_fields=["review_watch_limit_enabled"])
        request = self.factory.get("/report/")
        request.tenant = self.tenant
        force_authenticate(request, user=self.staff)
        response = ReviewWatchReportView.as_view()(request, video_id=self.video.pk)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data["enabled"])
        self.assertEqual([(row["student_id"], row["used_ms"]) for row in response.data["students"]], [(direct.pk, 120000), (self.student.pk, 60000)])
