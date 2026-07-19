from datetime import timedelta

from django.apps import apps
from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.models import TenantMembership
from apps.domains.clinic.tests import ClinicTestMixin
from apps.domains.results.models import Result, StudentReportedScore
from apps.domains.results.views.admin_student_performance_view import (
    AdminStudentPerformanceView,
)


User = get_user_model()


class StudentPerformanceConsoleTest(TestCase, ClinicTestMixin):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.data = self.setup_full_tenant("student-performance-console", student_count=1)
        self.tenant = self.data["tenant"]
        self.student = self.data["students"][0]
        self.enrollment = self.data["enrollments"][0]
        self.admin = User.objects.create_user(
            username="student_performance_console_admin",
            password="test1234",
            is_staff=True,
            tenant=self.tenant,
        )
        TenantMembership.ensure_active(
            tenant=self.tenant,
            user=self.admin,
            role="admin",
        )

    def _get(self, params=None, *, tenant=None):
        request = self.factory.get(
            "/results/admin/student-performance/",
            params or {},
        )
        request.tenant = tenant or self.tenant
        force_authenticate(request, user=self.admin)
        return AdminStudentPerformanceView.as_view()(request)

    def _score(self, *, title, score, days_ago, enrollment=None):
        exam_model = self.data["lec_session"].exams.model
        exam = exam_model.objects.create(
            tenant=self.tenant,
            title=title,
            exam_type=exam_model.ExamType.REGULAR,
            is_active=True,
            pass_score=60,
            max_score=100,
        )
        exam.sessions.add(self.data["lec_session"])
        return Result.objects.create(
            target_type="exam",
            target_id=exam.id,
            enrollment=enrollment or self.enrollment,
            total_score=score,
            max_score=100,
            submitted_at=timezone.now() - timedelta(days=days_ago),
        )

    def _evidence(self, suffix: str):
        inventory_file_model = apps.get_model("inventory", "InventoryFile")
        return inventory_file_model.objects.create(
            tenant=self.tenant,
            scope="student",
            student_ps=self.student.ps_number,
            display_name=f"성적표-{suffix}.jpg",
            original_name=f"score-{suffix}.jpg",
            r2_key=f"tenants/{self.tenant.id}/students/{self.student.ps_number}/scores/{suffix}.jpg",
            content_type="image/jpeg",
        )

    def test_new_results_automatically_extend_the_cumulative_student_summary(self):
        self._score(title="누적 테스트 1회", score=70, days_ago=3)
        self._score(title="누적 테스트 2회", score=80, days_ago=2)

        first = self._get({"days": "all"})

        self.assertEqual(first.status_code, 200, first.data)
        row = first.data["students"][0]
        self.assertEqual(row["scored_count"], 2)
        self.assertEqual(row["average_score_pct"], 75.0)
        self.assertEqual(row["latest_score_pct"], 80.0)
        self.assertEqual(row["change_pct_points"], 10.0)

        self._score(title="누적 테스트 3회", score=92, days_ago=1)
        refreshed = self._get({"days": "all"})

        refreshed_row = refreshed.data["students"][0]
        self.assertEqual(refreshed_row["scored_count"], 3)
        self.assertEqual(refreshed_row["latest_score_pct"], 92.0)
        self.assertEqual(refreshed_row["change_pct_points"], 12.0)
        self.assertEqual(refreshed.data["summary"]["result_count"], 3)

    def test_period_and_lecture_filters_recompute_the_roster_summary(self):
        self._score(title="기간 밖 시험", score=40, days_ago=60)
        self._score(title="최근 시험", score=90, days_ago=2)

        recent = self._get({"days": 30})
        recent_row = recent.data["students"][0]
        self.assertEqual(recent_row["scored_count"], 1)
        self.assertEqual(recent_row["latest_score_pct"], 90.0)

        selected = self._get({"days": "all", "lecture_id": self.data["lecture"].id})
        self.assertEqual(selected.status_code, 200, selected.data)
        self.assertEqual(selected.data["summary"]["student_count"], 1)
        self.assertEqual(selected.data["students"][0]["scored_count"], 2)

    def test_foreign_tenant_data_and_lecture_ids_are_not_exposed(self):
        other = self.setup_full_tenant("student-performance-console-other", student_count=1)
        exam_model = other["lec_session"].exams.model
        other_exam = exam_model.objects.create(
            tenant=other["tenant"],
            title="다른 학원 시험",
            exam_type=exam_model.ExamType.REGULAR,
            is_active=True,
            pass_score=60,
            max_score=100,
        )
        other_exam.sessions.add(other["lec_session"])
        Result.objects.create(
            target_type="exam",
            target_id=other_exam.id,
            enrollment=other["enrollments"][0],
            total_score=100,
            max_score=100,
        )

        response = self._get({"days": "all"})
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["summary"]["student_count"], 1)
        self.assertNotIn("다른 학원 시험", str(response.data))

        foreign_lecture = self._get({"lecture_id": other["lecture"].id})
        self.assertEqual(foreign_lecture.status_code, 404)

    def test_invalid_filter_values_fail_or_fall_back_safely(self):
        invalid_lecture = self._get({"lecture_id": "not-a-number"})
        self.assertEqual(invalid_lecture.status_code, 400)

        invalid_days = self._get({"days": "not-a-period"})
        self.assertEqual(invalid_days.status_code, 200)
        self.assertEqual(invalid_days.data["period"]["days"], 180)

    def test_query_count_does_not_scale_with_exam_history(self):
        self._score(title="쿼리 기준 시험", score=70, days_ago=10)
        with CaptureQueriesContext(connection) as initial_queries:
            initial = self._get({"days": "all"})
        self.assertEqual(initial.status_code, 200, initial.data)

        for index in range(2, 8):
            self._score(
                title=f"쿼리 누적 시험 {index}",
                score=70 + index,
                days_ago=10 - index,
            )
        with CaptureQueriesContext(connection) as expanded_queries:
            expanded = self._get({"days": "all"})

        self.assertEqual(expanded.status_code, 200, expanded.data)
        self.assertEqual(expanded.data["students"][0]["scored_count"], 7)
        self.assertLessEqual(len(expanded_queries), len(initial_queries) + 1)

    def test_identical_poll_uses_versioned_console_cache(self):
        self._score(title="캐시 확인 시험", score=82, days_ago=1)
        with CaptureQueriesContext(connection) as cold_queries:
            cold = self._get({"days": "all", "sort": "name"})
        with CaptureQueriesContext(connection) as warm_queries:
            warm = self._get({"days": "all", "sort": "name"})

        self.assertEqual(cold.status_code, 200, cold.data)
        self.assertEqual(warm.status_code, 200, warm.data)
        self.assertEqual(warm.data, cold.data)
        self.assertLess(len(warm_queries), len(cold_queries))

    def test_server_filters_and_pagination_bound_the_roster_payload(self):
        for index in range(1, 8):
            user = User.objects.create_user(
                username=f"performance_page_student_{index}",
                password="test1234",
                tenant=self.tenant,
            )
            self.student.__class__.objects.create(
                tenant=self.tenant,
                user=user,
                ps_number=f"PERF-PAGE-{index}",
                omr_code=f"{index:08d}",
                name=f"페이지학생{index}",
                grade=2 if index <= 4 else 3,
                school_type="HIGH",
                high_school="페이지고등학교",
                parent_phone=f"0109000{index:04d}",
            )

        page_two = self._get({"days": "all", "page": 2, "page_size": 3, "sort": "name"})
        self.assertEqual(page_two.status_code, 200, page_two.data)
        self.assertEqual(page_two.data["pagination"], {
            "page": 2,
            "page_size": 3,
            "total_count": 8,
            "total_pages": 3,
        })
        self.assertEqual(len(page_two.data["students"]), 3)

        searched = self._get({"days": "all", "search": "페이지학생3", "page_size": 3})
        self.assertEqual(searched.data["pagination"]["total_count"], 1)
        self.assertEqual(searched.data["students"][0]["name"], "페이지학생3")

        grade_filtered = self._get({"days": "all", "grade": 2, "page_size": 3})
        self.assertEqual(grade_filtered.data["pagination"]["total_count"], 4)
        self.assertTrue(all(row["grade"] == 2 for row in grade_filtered.data["students"]))

        invalid_page_size = self._get({"page_size": 101})
        self.assertEqual(invalid_page_size.status_code, 400)

    def test_source_summary_counts_only_the_selected_period_and_source(self):
        StudentReportedScore.objects.create(
            tenant=self.tenant,
            student=self.student,
            evidence_file=self._evidence("recent-school"),
            source=StudentReportedScore.Source.SCHOOL_EXAM,
            academic_year=timezone.now().year,
            semester=1,
            exam_round=StudentReportedScore.ExamRound.FIRST,
            exam_date=timezone.now().date() - timedelta(days=2),
            subject="수학",
            score=88,
            max_score=100,
            status=StudentReportedScore.Status.VERIFIED,
        )
        StudentReportedScore.objects.create(
            tenant=self.tenant,
            student=self.student,
            evidence_file=self._evidence("old-school"),
            source=StudentReportedScore.Source.SCHOOL_EXAM,
            academic_year=timezone.now().year - 1,
            semester=2,
            exam_round=StudentReportedScore.ExamRound.SECOND,
            exam_date=timezone.now().date() - timedelta(days=60),
            subject="수학",
            score=70,
            max_score=100,
            status=StudentReportedScore.Status.VERIFIED,
        )

        school = self._get({"days": 30, "source": "school"})

        self.assertEqual(school.status_code, 200, school.data)
        self.assertEqual(school.data["summary"]["result_count"], 1)
        self.assertEqual(school.data["summary"]["verified_school_score_count"], 1)
        self.assertEqual(school.data["summary"]["verified_mock_score_count"], 0)
        self.assertEqual(
            school.data["students"][0]["source_summaries"]["school"]["scored_count"],
            1,
        )

    def test_pending_review_queue_is_paginated_by_evidence_group(self):
        for index in range(3):
            StudentReportedScore.objects.create(
                tenant=self.tenant,
                student=self.student,
                evidence_file=self._evidence(f"pending-{index}"),
                source=StudentReportedScore.Source.SCHOOL_EXAM,
                academic_year=timezone.now().year,
                semester=1,
                exam_round=StudentReportedScore.ExamRound.FIRST,
                exam_date=timezone.now().date() - timedelta(days=index),
                subject=f"과목{index}",
                score=80 + index,
                max_score=100,
                status=StudentReportedScore.Status.PENDING,
            )

        first_page = self._get({"review_page": 1, "review_page_size": 2})
        second_page = self._get({"review_page": 2, "review_page_size": 2})

        self.assertEqual(first_page.status_code, 200, first_page.data)
        self.assertEqual(len(first_page.data["pending_reported_scores"]), 2)
        self.assertEqual(first_page.data["review_pagination"], {
            "page": 1,
            "page_size": 2,
            "total_count": 3,
            "total_rows": 3,
            "total_pages": 2,
        })
        self.assertEqual(len(second_page.data["pending_reported_scores"]), 1)
        self.assertEqual(second_page.data["review_pagination"]["page"], 2)
