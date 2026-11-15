from django.test import TestCase
from django.urls import resolve
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.domains.attendance.models import Attendance
from apps.domains.enrollment.models import SessionEnrollment
from apps.domains.enrollment.test_support import create_enrollment_fixture
from apps.domains.exams.models import Exam, ExamEnrollment
from apps.domains.lectures.models import Session
from apps.domains.lectures.test_support import create_lecture_fixture
from apps.domains.students.tests.test_student_domain_stabilization import _make_admin, _make_student, _make_tenant
from apps.domains.submissions.models import OmrUploadBatch, OmrUploadBatchItem, Submission


class ExamCandidateSessionScopeTests(TestCase):
    def setUp(self):
        self.tenant = _make_tenant("Scoped candidate", "candidate-session")
        self.admin = _make_admin(self.tenant, "candidate-session-admin")
        self.factory = APIRequestFactory()
        lecture = create_lecture_fixture(tenant=self.tenant, title="Candidate", name="Candidate")
        self.session = Session.objects.create(lecture=lecture, order=1, title="First")
        self.other_session = Session.objects.create(lecture=lecture, order=2, title="Second")
        self.unlinked_session = Session.objects.create(lecture=lecture, order=3, title="Unlinked")
        self.exam = Exam.objects.create(tenant=self.tenant, title="Shared exam", subject="MATH")
        self.exam.sessions.add(self.session, self.other_session)

        def enroll(tag, phone, *, state="ACTIVE", parent_phone="01001112222"):
            return create_enrollment_fixture(
                tenant=self.tenant, lecture=lecture, status=state,
                student=_make_student(self.tenant, tag, name=tag, phone=phone, parent_phone=parent_phone),
            )

        self.first = enroll("First candidate", "010-1234-5678", parent_phone="010-2468-1357")
        self.second = enroll("Second candidate", "01234567800")
        self.inactive = enroll("Inactive candidate", "01012345675", state="INACTIVE")
        self.attendance_only = enroll("Attendance candidate", "01012345679")
        for enrollment, session in (
            (self.first, self.session), (self.inactive, self.session), (self.second, self.other_session),
        ):
            SessionEnrollment.objects.create(tenant=self.tenant, session=session, enrollment=enrollment)
        Attendance.objects.create(tenant=self.tenant, session=self.session, enrollment=self.attendance_only)

    def call(self, request):
        request.tenant = self.tenant
        force_authenticate(request, user=self.admin)
        route = resolve(request.path)
        return route.func(request, **route.kwargs)

    def candidates(self, **query):
        return self.call(self.factory.get(
            f"/api/v1/submissions/submissions/exams/{self.exam.id}/candidates/", query,
        ))

    def set_explicit_roster(self):
        ExamEnrollment.objects.bulk_create([
            ExamEnrollment(exam=self.exam, enrollment=enrollment)
            for enrollment in (self.first, self.second, self.inactive, self.attendance_only)
        ])

    def make_scan(self, session=None, enrollment=None):
        submission = Submission.objects.create(
            tenant=self.tenant, user=self.admin, target_type="exam", target_id=self.exam.id,
            source="omr_scan", status=Submission.Status.NEEDS_IDENTIFICATION,
            enrollment_id=enrollment.id if enrollment else None,
        )
        if session:
            batch = OmrUploadBatch.objects.create(
                tenant=self.tenant, created_by=self.admin, exam_id=self.exam.id, total_count=2,
                session_id=session.id, lecture_id=session.lecture_id,
            )
            OmrUploadBatchItem.objects.create(
                batch=batch, tenant=self.tenant, exam_id=self.exam.id, ordinal=1,
                admission_status=OmrUploadBatchItem.AdmissionStatus.RECEIVED, submission=submission,
            )
        return submission

    def test_explicit_roster_is_intersected_with_active_session_membership(self):
        self.set_explicit_roster()
        response = self.candidates(session_id=self.session.id)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["session_id"], self.session.id)
        self.assertCountEqual(
            [row["enrollment_id"] for row in response.data["candidates"]],
            [self.first.id, self.attendance_only.id],
        )
        self.assertCountEqual(
            [row["enrollment_id"] for row in self.candidates().data],
            [self.first.id, self.second.id, self.attendance_only.id],
        )
        self.assertEqual(self.candidates(session_id=self.unlinked_session.id).status_code, 400)
        self.assertEqual(self.candidates(session_id="").status_code, 400)

    def test_legacy_roster_uses_current_session_and_exact_phone_suffix(self):
        for q in ("First", "5678", "12345678", "24681357"):
            response = self.candidates(session_id=self.session.id, q=q)
            self.assertEqual(response.status_code, 200, response.data)
            self.assertEqual([row["enrollment_id"] for row in response.data["candidates"]], [self.first.id])
        self.assertEqual([row["enrollment_id"] for row in self.candidates(q="12345678").data], [self.first.id])
        self.assertEqual([row["enrollment_id"] for row in self.candidates(session_id=self.session.id).data["candidates"]], [self.first.id])

    def test_manual_matching_and_duplicate_adoption_reject_other_session(self):
        self.set_explicit_roster()
        submission = Submission.objects.create(
            tenant=self.tenant, user=self.admin, target_type="exam", target_id=self.exam.id,
            source="omr_scan", status=Submission.Status.NEEDS_IDENTIFICATION,
        )
        endpoint = f"/api/v1/submissions/submissions/{submission.id}"
        response = self.call(self.factory.post(f"{endpoint}/manual-edit/", {
            "identifier": {"enrollment_id": self.second.id}, "session_id": self.session.id,
        }, format="json"))
        self.assertEqual(response.status_code, 400, response.data)
        submission.refresh_from_db()
        self.assertIsNone(submission.enrollment_id)
        self.assertEqual(submission.status, Submission.Status.NEEDS_IDENTIFICATION)

        submission.enrollment_id = self.second.id
        submission.save(update_fields=["enrollment_id"])
        response = self.call(self.factory.post(f"{endpoint}/accept-from-duplicates/", {
            "session_id": self.session.id,
        }, format="json"))
        self.assertEqual(response.status_code, 400, response.data)
        submission.refresh_from_db()
        self.assertEqual(submission.status, Submission.Status.NEEDS_IDENTIFICATION)

        submission.enrollment_id = None
        submission.save(update_fields=["enrollment_id"])
        response = self.call(self.factory.post(f"{endpoint}/manual-edit/", {
            "identifier": {"enrollment_id": self.attendance_only.id}, "session_id": self.session.id,
        }, format="json"))
        self.assertEqual(response.status_code, 200, response.data)
        submission.refresh_from_db()
        self.assertEqual(submission.enrollment_id, self.attendance_only.id)

    def test_queue_and_focus_use_original_scan_session_not_duplicate_or_latest_batch(self):
        local = self.make_scan(self.session)
        other = self.make_scan(self.other_session)
        historical = self.make_scan()
        OmrUploadBatchItem.objects.create(
            batch=local.omr_upload_batch_item.batch, tenant=self.tenant, exam_id=self.exam.id, ordinal=2,
            admission_status=OmrUploadBatchItem.AdmissionStatus.DUPLICATE, duplicate_of_submission=other,
        )
        endpoint = f"/api/v1/submissions/submissions/exams/{self.exam.id}/"

        def request(**query):
            response = self.call(self.factory.get(endpoint, query))
            self.assertEqual(response.status_code, 200, response.data)
            return response.data

        self.assertCountEqual([row["id"] for row in request()], [local.id, other.id, historical.id])
        self.assertCountEqual([row["id"] for row in request(session_id=self.session.id)], [local.id, historical.id])
        issues = request(session_id=self.session.id, review_issues=1)
        self.assertEqual(issues["total"], 2)
        self.assertCountEqual([row["id"] for row in issues["items"]], [local.id, historical.id])
        self.assertEqual(request(session_id=self.session.id, review_issues=1, focus_id=other.id)["total"], 0)
        self.assertEqual(request(session_id=self.session.id, review_issues=1, focus_id=local.id)["total"], 1)
        invalid = self.call(self.factory.get(endpoint, {"session_id": self.unlinked_session.id}))
        self.assertEqual(invalid.status_code, 400)

    def test_scoped_write_rejects_known_foreign_scan_and_prevents_cross_session_replacement(self):
        self.set_explicit_roster()
        other = self.make_scan(self.other_session, self.first)
        local = self.make_scan(self.session, self.first)

        def post(scanned, action, **body):
            return self.call(self.factory.post(f"/api/v1/submissions/submissions/{scanned.id}/{action}", {
                "session_id": self.session.id, **body,
            }, format="json"))

        for response in (
            post(other, "manual-edit/", identifier={"enrollment_id": self.first.id}),
            post(other, "accept-from-duplicates/"),
            post(local, "accept-from-duplicates/"),
            post(local, "manual-edit/", identifier={"enrollment_id": self.first.id}),
            post(local, "manual-edit/?allow_duplicate=1", identifier={"enrollment_id": self.first.id}),
        ):
            self.assertEqual(response.status_code, 409, response.data)
            self.assertEqual(response.data["code"], "OMR_REVIEW_SESSION_CONFLICT")
        for scanned in (local, other):
            original_updated_at = scanned.updated_at
            scanned.refresh_from_db()
            self.assertEqual(scanned.updated_at, original_updated_at)
            self.assertEqual(scanned.status, Submission.Status.NEEDS_IDENTIFICATION)

        unassigned = self.make_scan(self.session)
        response = post(unassigned, "manual-edit/", identifier={"enrollment_id": self.attendance_only.id})
        self.assertEqual(response.status_code, 200, response.data)
        unassigned.refresh_from_db()
        self.assertEqual(unassigned.enrollment_id, self.attendance_only.id)
