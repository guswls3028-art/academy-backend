from unittest.mock import patch

from django.db.models.query import QuerySet
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.domains.enrollment.test_support import create_enrollment_fixture
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
