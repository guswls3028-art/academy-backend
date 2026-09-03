from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.models import Tenant, TenantMembership
from apps.domains.enrollment.models import Enrollment
from apps.domains.exams.models import Exam, ExamEnrollment, ExamLecturePolicy, ExamQuestion, Sheet
from apps.domains.lectures.models import Lecture, Session
from apps.domains.parents.models import Parent
from apps.domains.results.models import ExamAttempt, Result, ResultFact, ResultItem
from apps.support.results.enterprise_analytics import (
    build_student_enterprise_analytics,
    normalize_analytics_days,
)
from apps.domains.results.views.admin_enterprise_analytics_view import AdminEnterpriseAnalyticsView
from apps.domains.results.views.admin_student_grades_view import AdminStudentGradesView
from apps.domains.student_app.results.views import MyGradesAnalyticsView, MyGradesSummaryView
from apps.domains.students.models import Student
from apps.domains.submissions.models import Submission


User = get_user_model()


class EnterpriseAnalyticsTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.tenant = Tenant.objects.create(code="enterprise-analytics", name="Enterprise Analytics", is_active=True)
        self.other_tenant = Tenant.objects.create(
            code="enterprise-analytics-other",
            name="Enterprise Analytics Other",
            is_active=True,
        )
        self.admin = User.objects.create_user(
            username="enterprise-analytics-admin",
            password="pw1234",
            tenant=self.tenant,
            is_staff=True,
        )
        TenantMembership.ensure_active(tenant=self.tenant, user=self.admin, role="owner")

    def _student(self, tenant: Tenant, suffix: str, *, parent: Parent | None = None) -> Student:
        user = User.objects.create_user(
            username=f"enterprise-analytics-student-{tenant.code}-{suffix}",
            password="pw1234",
            tenant=tenant,
            name=f"Student {suffix}",
        )
        TenantMembership.ensure_active(tenant=tenant, user=user, role="student")
        return Student.objects.create(
            tenant=tenant,
            user=user,
            parent=parent,
            ps_number=f"EA-{tenant.id}-{suffix}",
            omr_code=f"EA{tenant.id}{suffix}".replace("-", "")[:8].ljust(8, "0"),
            name=f"Student {suffix}",
            parent_phone="01012345678",
        )

    def _exam_result_for_student(
        self,
        *,
        tenant: Tenant,
        student: Student,
        title: str,
        score: float,
        max_score: float = 100,
        pass_score: float = 60,
    ) -> tuple[Exam, Enrollment, Result]:
        lecture = Lecture.objects.create(
            tenant=tenant,
            title=f"{title} Lecture",
            name=f"{title} Lecture",
            subject="MATH",
        )
        session = Session.objects.create(lecture=lecture, order=1, title="1회")
        enrollment = Enrollment.objects.create(
            tenant=tenant,
            student=student,
            lecture=lecture,
            status="ACTIVE",
        )
        exam = Exam.objects.create(
            tenant=tenant,
            title=title,
            exam_type=Exam.ExamType.REGULAR,
            is_active=True,
            pass_score=pass_score,
            max_score=max_score,
        )
        exam.sessions.add(session)
        ExamEnrollment.objects.create(exam=exam, enrollment=enrollment)
        sheet = Sheet.objects.create(exam=exam, name="MAIN", total_questions=1)
        question = ExamQuestion.objects.create(sheet=sheet, number=1, score=max_score)
        result = Result.objects.create(
            target_type="exam",
            target_id=exam.id,
            enrollment=enrollment,
            total_score=score,
            max_score=max_score,
            objective_score=score,
        )
        ResultItem.objects.create(
            result=result,
            question=question,
            answer="1",
            is_correct=score > 0,
            score=score,
            max_score=max_score,
            source="manual",
        )
        return exam, enrollment, result

    def test_normalize_analytics_days_clamps_public_input(self):
        self.assertEqual(normalize_analytics_days("1"), 30)
        self.assertEqual(normalize_analytics_days("9999"), 730)
        self.assertEqual(normalize_analytics_days("bad", default=365), 365)

    def test_admin_analytics_is_tenant_scoped_and_filters_demo_exams(self):
        student = self._student(self.tenant, "main")
        exam, enrollment, _ = self._exam_result_for_student(
            tenant=self.tenant,
            student=student,
            title="Real Exam",
            score=80,
        )
        self._exam_result_for_student(
            tenant=self.tenant,
            student=student,
            title="[E2E-123] Demo Exam",
            score=10,
        )
        other_student = self._student(self.other_tenant, "other")
        self._exam_result_for_student(
            tenant=self.other_tenant,
            student=other_student,
            title="Other Tenant Exam",
            score=100,
        )
        ResultFact.objects.create(
            target_type="exam",
            target_id=exam.id,
            enrollment=enrollment,
            submission_id=1,
            question_id=1,
            score=80,
            max_score=100,
            source="manual",
        )
        Submission.objects.create(
            tenant=self.tenant,
            user=student.user,
            enrollment=enrollment,
            target_type=Submission.TargetType.EXAM,
            target_id=exam.id,
            source=Submission.Source.ONLINE,
            status=Submission.Status.DONE,
        )

        request = self.factory.get("/api/v1/results/admin/analytics/")
        force_authenticate(request, user=self.admin)
        request.tenant = self.tenant

        response = AdminEnterpriseAnalyticsView.as_view()(request)

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["summary"]["exam_result_count"], 1)
        self.assertEqual(response.data["summary"]["avg_score_pct"], 80.0)
        self.assertEqual(response.data["summary"]["pass_rate_pct"], 100.0)
        self.assertEqual(response.data["usage"]["manual_score_events"], 1)
        self.assertEqual(response.data["usage"]["auto_grade_submissions"], 1)
        self.assertEqual(response.data["usage"]["source_breakdown"], {"online": 1})
        self.assertEqual(response.data["data_quality"]["clean_exam_count"], 1)
        self.assertEqual(response.data["data_quality"]["filtered_test_exam_count"], 1)
        self.assertEqual([row["title"] for row in response.data["top_exams"]], ["Real Exam"])

    def test_admin_analytics_excludes_stale_non_target_result(self):
        target_student = self._student(self.tenant, "target")
        exam, target_enrollment, _ = self._exam_result_for_student(
            tenant=self.tenant,
            student=target_student,
            title="Targeted Exam",
            score=80,
        )
        stale_student = self._student(self.tenant, "stale")
        stale_enrollment = Enrollment.objects.create(
            tenant=self.tenant,
            student=stale_student,
            lecture=target_enrollment.lecture,
            status="ACTIVE",
        )
        Result.objects.create(
            target_type="exam",
            target_id=exam.id,
            enrollment=stale_enrollment,
            total_score=0,
            max_score=100,
            objective_score=0,
        )
        request = self.factory.get("/api/v1/results/admin/analytics/")
        force_authenticate(request, user=self.admin)
        request.tenant = self.tenant

        response = AdminEnterpriseAnalyticsView.as_view()(request)

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["summary"]["exam_result_count"], 1)
        self.assertEqual(response.data["summary"]["avg_score_pct"], 80.0)
        self.assertEqual(response.data["summary"]["pass_rate_pct"], 100.0)

    def test_admin_history_and_analytics_use_lecture_pass_score(self):
        student = self._student(self.tenant, "lecture-cutoff")
        exam, enrollment, _ = self._exam_result_for_student(
            tenant=self.tenant,
            student=student,
            title="Shared Cutoff",
            score=65,
            pass_score=60,
        )
        ExamLecturePolicy.objects.create(
            exam=exam,
            lecture=enrollment.lecture,
            pass_score=70,
        )
        history_request = self.factory.get(
            "/api/v1/results/admin/student-grades/",
            {"student_id": student.id},
        )
        force_authenticate(history_request, user=self.admin)
        history_request.tenant = self.tenant
        history = AdminStudentGradesView.as_view()(history_request)
        analytics_request = self.factory.get("/api/v1/results/admin/analytics/")
        force_authenticate(analytics_request, user=self.admin)
        analytics_request.tenant = self.tenant
        analytics = AdminEnterpriseAnalyticsView.as_view()(analytics_request)

        self.assertEqual(history.status_code, 200, history.data)
        self.assertFalse(history.data["exams"][0]["is_pass"])
        self.assertEqual(analytics.status_code, 200, analytics.data)
        self.assertEqual(analytics.data["summary"]["pass_rate_pct"], 0.0)
        self.assertEqual(analytics.data["top_exams"][0]["pass_rate_pct"], 0.0)

    def test_admin_analytics_uses_initial_score_after_retake(self):
        student = self._student(self.tenant, "retake")
        exam, enrollment, result = self._exam_result_for_student(
            tenant=self.tenant,
            student=student,
            title="Retake Separated Exam",
            score=100,
        )
        ExamAttempt.objects.create(
            exam=exam,
            enrollment=enrollment,
            attempt_index=1,
            is_representative=False,
            status="done",
            meta={
                "initial_snapshot": {
                    "total_score": 25.0,
                    "max_score": 100.0,
                    "source": "test",
                },
                "total_score": 25.0,
                "max_score": 100.0,
            },
        )
        retake = ExamAttempt.objects.create(
            exam=exam,
            enrollment=enrollment,
            attempt_index=2,
            is_retake=True,
            is_representative=True,
            status="done",
            meta={"total_score": 100.0, "max_score": 100.0},
        )
        result.attempt = retake
        result.save(update_fields=["attempt", "updated_at"])

        request = self.factory.get("/api/v1/results/admin/analytics/")
        force_authenticate(request, user=self.admin)
        request.tenant = self.tenant

        response = AdminEnterpriseAnalyticsView.as_view()(request)

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["summary"]["avg_score_pct"], 25.0)
        self.assertEqual(response.data["summary"]["pass_rate_pct"], 0.0)
        self.assertEqual(response.data["top_exams"][0]["avg_score_pct"], 25.0)

    def test_parent_student_analytics_uses_selected_child_only(self):
        parent_user = User.objects.create_user(
            username="enterprise-analytics-parent",
            password="pw1234",
            tenant=self.tenant,
        )
        TenantMembership.ensure_active(tenant=self.tenant, user=parent_user, role="parent")
        parent = Parent.objects.create(
            tenant=self.tenant,
            user=parent_user,
            name="Parent",
            phone="01055556666",
        )
        child_a = self._student(self.tenant, "child-a", parent=parent)
        child_b = self._student(self.tenant, "child-b", parent=parent)
        self._exam_result_for_student(
            tenant=self.tenant,
            student=child_a,
            title="Child A Exam",
            score=90,
        )
        self._exam_result_for_student(
            tenant=self.tenant,
            student=child_b,
            title="Child B Exam",
            score=40,
        )

        request = self.factory.get(
            "/api/v1/student/grades/analytics/",
            HTTP_X_STUDENT_ID=str(child_a.id),
        )
        force_authenticate(request, user=parent_user)
        request.tenant = self.tenant

        response = MyGradesAnalyticsView.as_view()(request)

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["student"]["id"], child_a.id)
        self.assertEqual(response.data["summary"]["scored_exam_count"], 1)
        self.assertEqual(response.data["summary"]["avg_score_pct"], 90.0)
        self.assertEqual([row["title"] for row in response.data["trends"]], ["Child A Exam"])

        summary_request = self.factory.get(
            "/api/v1/student/grades/",
            HTTP_X_STUDENT_ID=str(child_b.id),
        )
        force_authenticate(summary_request, user=parent_user)
        summary_request.tenant = self.tenant

        summary_response = MyGradesSummaryView.as_view()(summary_request)

        self.assertEqual(summary_response.status_code, 200, summary_response.data)
        self.assertEqual(
            [row["title"] for row in summary_response.data["exam_trend"]],
            ["Child B Exam"],
        )
        self.assertEqual(summary_response.data["exam_summary"]["latest_score_pct"], 40.0)

        admin_request = self.factory.get(
            "/api/v1/results/admin/student-grades/",
            {"student_id": child_b.id},
        )
        force_authenticate(admin_request, user=self.admin)
        admin_request.tenant = self.tenant
        admin_response = AdminStudentGradesView.as_view()(admin_request)

        self.assertEqual(admin_response.status_code, 200, admin_response.data)
        self.assertEqual(admin_response.data["exam_trend"], summary_response.data["exam_trend"])
        self.assertEqual(admin_response.data["exam_summary"], summary_response.data["exam_summary"])

        unlinked_child = self._student(self.tenant, "unlinked")
        for invalid_header in ("not-a-number", str(unlinked_child.id)):
            invalid_request = self.factory.get(
                "/api/v1/student/grades/analytics/",
                HTTP_X_STUDENT_ID=invalid_header,
            )
            force_authenticate(invalid_request, user=parent_user)
            invalid_request.tenant = self.tenant
            invalid_response = MyGradesAnalyticsView.as_view()(invalid_request)
            self.assertEqual(invalid_response.status_code, 403)

    def test_unpublished_exam_is_hidden_from_student_summary_but_not_admin(self):
        student = self._student(self.tenant, "publication")
        self._exam_result_for_student(
            tenant=self.tenant,
            student=student,
            title="Published Exam",
            score=88,
        )
        hidden_exam, _, _ = self._exam_result_for_student(
            tenant=self.tenant,
            student=student,
            title="Staff Only Exam",
            score=41,
        )
        hidden_exam.student_results_published = False
        hidden_exam.save(update_fields=["student_results_published", "updated_at"])

        summary_request = self.factory.get("/api/v1/student/grades/")
        force_authenticate(summary_request, user=student.user)
        summary_request.tenant = self.tenant
        summary_response = MyGradesSummaryView.as_view()(summary_request)

        analytics_request = self.factory.get("/api/v1/student/grades/analytics/")
        force_authenticate(analytics_request, user=student.user)
        analytics_request.tenant = self.tenant
        analytics_response = MyGradesAnalyticsView.as_view()(analytics_request)

        admin_request = self.factory.get(
            "/api/v1/results/admin/student-grades/",
            {"student_id": student.id},
        )
        force_authenticate(admin_request, user=self.admin)
        admin_request.tenant = self.tenant
        admin_response = AdminStudentGradesView.as_view()(admin_request)

        self.assertEqual(summary_response.status_code, 200, summary_response.data)
        self.assertEqual(
            [row["title"] for row in summary_response.data["exams"]],
            ["Published Exam"],
        )
        self.assertEqual(
            [row["title"] for row in summary_response.data["exam_trend"]],
            ["Published Exam"],
        )
        self.assertEqual(analytics_response.status_code, 200, analytics_response.data)
        self.assertEqual(
            [row["title"] for row in analytics_response.data["trends"]],
            ["Published Exam"],
        )
        self.assertEqual(admin_response.status_code, 200, admin_response.data)
        self.assertEqual(
            {row["title"] for row in admin_response.data["exams"]},
            {"Published Exam", "Staff Only Exam"},
        )

    def test_student_analytics_applies_date_window_to_all_metrics(self):
        now = timezone.now()
        recent = (now - timedelta(days=5)).isoformat()
        old = (now - timedelta(days=90)).isoformat()
        summary = {
            "exams": [
                {
                    "exam_id": 1,
                    "title": "최근 합격",
                    "total_score": 90,
                    "max_score": 100,
                    "achievement": "PASS",
                    "is_pass": True,
                    "recorded_at": recent,
                    "submitted_at": None,
                    "wrong_question_numbers": [1],
                },
                {
                    "exam_id": 2,
                    "title": "오래된 불합격",
                    "total_score": 10,
                    "max_score": 100,
                    "achievement": "FAIL",
                    "is_pass": False,
                    "recorded_at": old,
                    "submitted_at": None,
                    "wrong_question_numbers": [9],
                },
                {
                    "exam_id": 3,
                    "title": "오래된 미응시",
                    "total_score": None,
                    "max_score": 100,
                    "achievement": "NOT_SUBMITTED",
                    "meta_status": "NOT_SUBMITTED",
                    "recorded_at": old,
                    "submitted_at": None,
                    "wrong_question_numbers": [],
                },
            ],
            "homeworks": [
                {
                    "score": 5,
                    "max_score": 10,
                    "achievement": "FAIL",
                    "passed": False,
                    "recorded_at": recent,
                },
                {
                    "score": 10,
                    "max_score": 10,
                    "achievement": "PASS",
                    "passed": True,
                    "recorded_at": old,
                },
            ],
        }
        with patch(
            "apps.support.results.enterprise_analytics.build_student_grades_summary",
            return_value=summary,
        ):
            analytics = build_student_enterprise_analytics(
                tenant=self.tenant,
                student=SimpleNamespace(id=99, name="기간 학생"),
                days=30,
            )

        self.assertEqual(analytics["summary"]["exam_count"], 1)
        self.assertEqual(analytics["summary"]["pass_rate_pct"], 100.0)
        self.assertEqual(analytics["summary"]["not_submitted_count"], 0)
        self.assertEqual(analytics["weak_questions"], [{"question_number": 1, "wrong_count": 1}])
        self.assertEqual(analytics["homework"]["assigned_count"], 1)
        self.assertEqual(analytics["homework"]["graded_count"], 1)
        self.assertEqual(analytics["homework"]["avg_score_pct"], 50.0)
        self.assertEqual(analytics["data_quality"]["filtered_test_exam_count"], 0)

    def test_legitimate_test_title_is_not_filtered_as_synthetic_data(self):
        student = self._student(self.tenant, "legitimate-test")
        self._exam_result_for_student(
            tenant=self.tenant,
            student=student,
            title="Ymath 주간 테스트 1회",
            score=84,
        )
        self._exam_result_for_student(
            tenant=self.tenant,
            student=student,
            title="[E2E-456] synthetic fixture",
            score=10,
        )

        request = self.factory.get("/api/v1/results/admin/analytics/")
        force_authenticate(request, user=self.admin)
        request.tenant = self.tenant

        response = AdminEnterpriseAnalyticsView.as_view()(request)

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["summary"]["exam_result_count"], 1)
        self.assertEqual(response.data["summary"]["avg_score_pct"], 84.0)
        self.assertEqual(
            [row["title"] for row in response.data["top_exams"]],
            ["Ymath 주간 테스트 1회"],
        )
        self.assertEqual(response.data["data_quality"]["filtered_test_exam_count"], 1)
