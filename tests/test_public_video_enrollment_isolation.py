from datetime import date

from django.utils import timezone
from rest_framework.test import APITestCase

from apps.core.models import Tenant, TenantMembership, User
from apps.domains.enrollment.models import Enrollment, SessionEnrollment
from apps.domains.fees.models import FeePayment, FeeTemplate, StudentFee, StudentInvoice
from apps.domains.lectures.models import Lecture, Session
from apps.domains.parents.models import Parent
from apps.domains.students.models import Student
from apps.domains.video.models import AccessMode, Video, VideoAccess, VideoProgress


class PublicVideoEnrollmentIsolationTests(APITestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(code="qa-public-record", name="QA", is_active=True)
        self.parent = self.user("parent", role="parent")
        parent = Parent.objects.create(tenant=self.tenant, user=self.parent, name="QA", phone="01000000001")
        self.student, self.sibling = [
            Student.objects.create(tenant=self.tenant, parent=parent, user=self.user(f"child-{i}"),
                                   name=f"QA {i}", ps_number=f"QP{i}", omr_code=f"1234567{i}",
                                   parent_phone=parent.phone) for i in (1, 2)
        ]
        self.lecture = Lecture.objects.create(tenant=self.tenant, name="QA", title="QA", subject="MATH")
        first = Session.objects.create(lecture=self.lecture, order=1, title="First")
        second = Session.objects.create(lecture=self.lecture, order=2, title="Second")
        self.public = Video.objects.create(tenant=self.tenant, session=first, title="Public", duration=100,
                                           visibility=Video.Visibility.PUBLIC, status=Video.Status.READY)
        self.private = Video.objects.create(tenant=self.tenant, session=second, title="Enrolled", duration=100,
                                            visibility=Video.Visibility.ENROLLED, status=Video.Status.READY)
        self.headers = {"HTTP_HOST":"localhost", "HTTP_X_TENANT_CODE":self.tenant.code}
        self.client.force_authenticate(self.student.user)

    def user(self, suffix, role="student"):
        user = User.objects.create_user(username=f"qa-video-{suffix}", tenant=self.tenant)
        TenantMembership.ensure_active(tenant=self.tenant, user=user, role=role)
        return user

    def video_path(self, video=None, action="playback"):
        return f"/api/v1/student/video/videos/{(video or self.public).pk}/{action}/"

    def post(self, path, data=None, expected=200):
        result = self.client.post(path, data or {}, format="json", **self.headers)
        self.assertEqual(result.status_code, expected, result.data)
        return result.data

    def access(self, video=None, expected=200):
        result = self.client.get(self.video_path(video) + "?access_check=true", **self.headers)
        self.assertEqual(result.status_code, expected, result.data)

    def as_parent(self, child):
        self.client.force_authenticate(self.parent)
        self.headers["HTTP_X_STUDENT_ID"] = str(child.pk)

    def test_public_view_uses_only_hidden_record_and_binds_parent_refresh_to_selected_child(self):
        self.access()
        self.assertFalse(Enrollment.objects.filter(student=self.student).exists())
        self.assertFalse(Lecture.objects.filter(tenant=self.tenant, is_system=True).exists())
        opened = self.post(self.video_path())
        record = Enrollment.objects.get(pk=opened["video"]["enrollment_id"])
        self.assertTrue(record.lecture.is_system)
        self.assertEqual((record.tenant_id, record.student_id), (self.tenant.pk, self.student.pk))
        self.assertEqual(self.public.session.lecture_id, self.lecture.pk)
        self.post("/api/v1/media/playback/refresh/", {"token":opened["playback_token"]})
        self.as_parent(self.student)
        self.access()
        saved = self.post(self.video_path(action="progress"), {"progress":0.4, "last_position":40})
        resumed = self.post(self.video_path())
        self.assertEqual((saved["enrollment_id"], resumed["video"]["last_position"]), (record.pk, 40))
        for action in ("refresh", "renew"):
            self.post(f"/api/v1/media/playback/{action}/", {"token":resumed["playback_token"]})
        self.access(self.private, expected=403)
        self.as_parent(self.sibling)
        self.post("/api/v1/media/playback/refresh/", {"token":resumed["playback_token"]}, expected=403)
        self.assertFalse(Enrollment.objects.filter(lecture=self.lecture).exists())
        self.assertFalse(Enrollment.objects.filter(student=self.sibling).exists())
        self.assertFalse(SessionEnrollment.objects.exists())

    def check_existing_record(self, status):
        record = Enrollment.objects.create(tenant=self.tenant, student=self.student, lecture=self.lecture, status=status,
                                           lecture_memo="Preserve this enrollment memo")
        progress = VideoProgress.objects.create(video=self.public, enrollment=record, progress=0.25, last_position=25)
        fee = FeeTemplate.objects.create(tenant=self.tenant, name="QA", fee_type="TUITION", amount=100,
                                         lecture=self.lecture, auto_assign=True)
        StudentFee.objects.create(tenant=self.tenant, student=self.student, fee_template=fee, enrollment=record,
                                  is_active=False)
        invoice = StudentInvoice.objects.create(tenant=self.tenant, student=self.student, invoice_number="QA-01",
                                                billing_year=2026, billing_month=1, total_amount=100, paid_amount=20,
                                                due_date=date(2026, 1, 20))
        FeePayment.objects.create(tenant=self.tenant, student=self.student, invoice=invoice, amount=20,
                                  payment_method="CASH", paid_at=timezone.now(), receipt_note="Keep cash receipt")
        def ledger():
            return [list(model.objects.filter(tenant=self.tenant).values()) for model in (StudentFee, StudentInvoice, FeePayment)]
        before = ledger()
        self.access()
        opened = self.post(self.video_path())
        self.assertEqual((opened["video"]["enrollment_id"], opened["video"]["last_position"]), (record.pk, 25))
        self.post("/api/v1/media/playback/refresh/", {"token":opened["playback_token"]})
        self.as_parent(self.student)
        self.access()
        saved = self.post(self.video_path(action="progress"), {"progress":0.6, "last_position":60})
        self.assertEqual((saved["id"], saved["enrollment_id"]), (progress.pk, record.pk))
        resumed = self.post(self.video_path())
        self.assertEqual(resumed["video"]["last_position"], 60)
        for action in ("refresh", "renew"):
            self.post(f"/api/v1/media/playback/{action}/", {"token":resumed["playback_token"]})
        self.access(self.private, expected=403)
        VideoAccess.objects.create(video=self.public, enrollment=record, access_mode=AccessMode.BLOCKED, is_override=True)
        self.access(expected=403)
        self.post(self.video_path(), expected=403)
        self.post(self.video_path(action="progress"), {"last_position":80}, expected=403)
        self.post("/api/v1/media/playback/refresh/", {"token":resumed["playback_token"]}, expected=403)
        record.refresh_from_db()
        progress.refresh_from_db()
        self.assertEqual((record.status, record.lecture_memo, progress.last_position), (status, "Preserve this enrollment memo", 60))
        self.assertEqual(Enrollment.objects.filter(student=self.student).count(), 1)
        self.assertEqual(ledger(), before)

    def test_inactive_record_retains_progress_policy_and_billing(self):
        self.check_existing_record("INACTIVE")

    def test_pending_record_retains_progress_policy_and_billing(self):
        self.check_existing_record("PENDING")

    def test_later_course_registration_preserves_one_public_progress_row_and_both_block_rules(self):
        original = self.post(self.video_path(action="progress"), {"progress":0.35, "last_position":35})
        library_id = original["enrollment_id"]
        first_token = self.post(self.video_path())["playback_token"]
        self.client.force_authenticate(self.user("staff", role="admin"))
        course = self.post("/api/v1/enrollments/bulk_create/",
                           {"lecture":self.lecture.pk, "students":[self.student.pk]}, expected=201)[0]
        self.assertNotEqual(course["id"], library_id)
        self.post("/api/v1/lectures/attendance/bulk_create/",
                  {"session":self.public.session_id, "students":[self.student.pk]}, expected=201)
        self.client.force_authenticate(self.student.user)
        self.post("/api/v1/media/playback/refresh/", {"token":first_token})
        self.as_parent(self.student)
        self.access()
        current = self.post(self.video_path() + f"?enrollment={course['id']}")
        self.assertEqual((current["video"]["enrollment_id"], current["video"]["last_position"]), (library_id, 35))
        self.post("/api/v1/media/playback/refresh/", {"token":current["playback_token"]})
        self.client.force_authenticate(self.student.user)
        self.headers.pop("HTTP_X_STUDENT_ID")
        saved = self.post(self.video_path(action="progress"), {"enrollment_id":course["id"], "progress":0.7, "last_position":70})
        self.assertEqual((saved["id"], saved["enrollment_id"]), (original["id"], library_id))
        self.assertEqual(self.post(self.video_path())["video"]["last_position"], 70)
        self.assertEqual(VideoProgress.objects.filter(video=self.public).count(), 1)
        catalog_path = f"/api/v1/student/video/sessions/{self.public.session_id}/videos/"
        catalog = self.client.get(catalog_path, **self.headers)
        self.assertEqual(catalog.status_code, 200, catalog.data)
        self.assertEqual(catalog.data["items"][0]["last_position"], 70)
        stats = self.client.get("/api/v1/student/video/me/stats/", **self.headers)
        self.assertEqual(stats.status_code, 200, stats.data)
        self.assertEqual((stats.data["total_videos"], stats.data["total_watch_duration"]), (1, 70))
        VideoAccess.objects.create(video=self.public, enrollment_id=course["id"], rule="blocked")
        self.access(expected=403)
        self.post(self.video_path(action="progress"), {"last_position":80}, expected=403)
        progress = VideoProgress.objects.get(pk=original["id"])
        self.assertEqual(progress.last_position, 70)

        # An existing course record remains protected by its library-side rule too.
        other = Video.objects.create(tenant=self.tenant, session=self.public.session, order=2, title="Second public",
                                     status=Video.Status.READY, visibility=Video.Visibility.PUBLIC, duration=100)
        VideoProgress.objects.create(video=other, enrollment_id=course["id"], progress=0.2, last_position=20)
        VideoAccess.objects.create(video=other, enrollment_id=library_id, access_mode=AccessMode.BLOCKED)
        self.as_parent(self.student)
        self.access(other, expected=403)
        self.post(self.video_path(other), expected=403)
        listed = self.client.get(catalog_path, **self.headers)
        self.assertEqual(listed.status_code, 200, listed.data)
        self.assertEqual([item["access_mode"] for item in listed.data["items"]], ["BLOCKED", "BLOCKED"])

    def test_returning_to_enrolled_invalidates_public_grant_even_for_current_course_member(self):
        record = Enrollment.objects.create(tenant=self.tenant, student=self.student, lecture=self.lecture)
        SessionEnrollment.objects.create(tenant=self.tenant, enrollment=record, session=self.public.session)
        self.as_parent(self.student)
        opened = self.post(self.video_path())
        self.client.force_authenticate(self.user("visibility-admin", role="admin"))
        self.public.refresh_from_db()
        prior_version = self.public.policy_version
        changed = self.client.patch(f"/api/v1/media/videos/{self.public.pk}/", {"visibility":"ENROLLED"},
                                    format="json", HTTP_X_EXPECTED_UPDATED_AT=self.public.updated_at.isoformat(), **self.headers)
        self.assertEqual(changed.status_code, 200, changed.data)
        self.public.refresh_from_db()
        self.assertEqual(self.public.policy_version, prior_version + 1)
        self.as_parent(self.student)
        for action in ("refresh", "renew"):
            self.post(f"/api/v1/media/playback/{action}/", {"token":opened["playback_token"]}, expected=403)
        self.assertEqual(self.post(self.video_path())["video"]["enrollment_id"], record.pk)

    def test_public_video_is_scoped_to_the_same_tenant(self):
        other = Tenant.objects.create(code="qa-other-public", name="QA", is_active=True)
        foreign = Video.objects.create(tenant=other, title="Other public", status=Video.Status.READY,
                                       visibility=Video.Visibility.PUBLIC, duration=100)
        self.as_parent(self.student)
        self.access(foreign, expected=404)
        self.post(self.video_path(foreign), expected=404)
        self.assertFalse(Enrollment.objects.exists())
