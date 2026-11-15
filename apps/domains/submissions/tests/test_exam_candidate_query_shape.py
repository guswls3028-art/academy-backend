from unittest.mock import patch

from django.db.models.query import QuerySet
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
from apps.domains.submissions.models import Submission
from apps.domains.submissions.views.exam_candidates_view import ExamCandidatesView
from apps.support.submissions.candidate_dependencies import CandidateRows


class ExamCandidateQueryShapeTests(TestCase):
    def setUp(self):
        self.tenant = _make_tenant("Candidate query", "candidate-query")
        self.other = _make_tenant("Other query", "other-query")
        self.admin = _make_admin(self.tenant, "candidate-query-admin")
        self.factory = APIRequestFactory()
        lecture = create_lecture_fixture(tenant=self.tenant, title="Query", name="Query")
        self.enrollment_ids = [create_enrollment_fixture(
            tenant=self.tenant, lecture=lecture,
            student=_make_student(self.tenant, f"C{idx}", phone=f"0108899000{idx}"),
            status="ACTIVE",
        ).id for idx in range(3)]

    def request_candidates(self, rows):
        request = self.factory.get("/api/v1/submissions/submissions/exams/123/candidates/?q=student")
        request.tenant = self.tenant
        force_authenticate(request, user=self.admin)
        materialized = []
        original = QuerySet._fetch_all

        def count_rows(queryset):
            fresh = queryset._result_cache is None
            original(queryset)
            if fresh and queryset.model is Submission:
                materialized.append(len(queryset._result_cache))

        with patch("apps.domains.submissions.views.exam_candidates_view.exam_candidate_rows",
                   return_value=CandidateRows(found=True, rows=rows)), patch.object(QuerySet, "_fetch_all", count_rows):
            response = ExamCandidatesView.as_view()(request, exam_id=123)
        return response, materialized

    def test_match_lookup_is_bounded_to_displayed_candidates_and_tenant(self):
        matched, unmatched, unrelated = self.enrollment_ids
        # Unrelated and repeated scans must not inflate the search's result set.
        submissions = [Submission(tenant=self.tenant, user=self.admin, target_type="exam", target_id=123,
                                  source="omr_scan", enrollment_id=unrelated) for _ in range(120)]
        submissions += [Submission(tenant=self.tenant, user=self.admin, target_type="exam", target_id=123,
                                   source="omr_scan", enrollment_id=matched) for _ in range(8)]
        submissions += [Submission(tenant=self.other, user=self.admin, target_type="exam", target_id=123,
                                   source="omr_scan", enrollment_id=unmatched),
                        Submission(tenant=self.tenant, user=self.admin, target_type="exam", target_id=456,
                                   source="omr_scan", enrollment_id=unmatched)]
        Submission.objects.bulk_create(submissions)
        rows = [{"enrollment_id": matched, "student_name": "first"}, {"enrollment_id": unmatched, "student_name": "second"}]
        response, materialized = self.request_candidates(rows)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, [{**rows[0], "already_matched": True}, {**rows[1], "already_matched": False}])
        self.assertEqual(materialized, [1], "Only one distinct matching enrollment should leave the database")
        self.assertEqual(Submission.objects.count(), 130, "Read-only lookup must preserve every original submission")

    def test_empty_search_does_not_fetch_historical_matches(self):
        Submission.objects.create(tenant=self.tenant, user=self.admin, target_type="exam", target_id=123,
                                  source="omr_scan", enrollment_id=self.enrollment_ids[0])
        response, materialized = self.request_candidates([])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, [])
        self.assertEqual(sum(materialized), 0)


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
