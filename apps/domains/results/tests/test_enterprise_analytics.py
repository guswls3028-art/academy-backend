from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.models import Tenant, TenantMembership
from apps.domains.enrollment.models import Enrollment
from apps.domains.exams.models import Exam, ExamEnrollment, ExamQuestion, Sheet
from apps.domains.lectures.models import Lecture, Session
from apps.domains.parents.models import Parent
from apps.domains.results.models import Result, ResultFact, ResultItem
from apps.domains.results.services.enterprise_analytics import normalize_analytics_days
from apps.domains.results.views.admin_enterprise_analytics_view import AdminEnterpriseAnalyticsView
from apps.domains.student_app.results.views import MyGradesAnalyticsView
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
