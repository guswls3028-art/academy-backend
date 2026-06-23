from __future__ import annotations

from django.apps import apps as django_apps
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.domains.results.views.admin_landing_stats_view import AdminResultsLandingStatsView


class AdminLandingStatsTenantMetadataTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()

        tenant_model = django_apps.get_model("core", "Tenant")
        membership_model = django_apps.get_model("core", "TenantMembership")
        student_model = django_apps.get_model("students", "Student")
        lecture_model = django_apps.get_model("lectures", "Lecture")
        session_model = django_apps.get_model("lectures", "Session")
        enrollment_model = django_apps.get_model("enrollment", "Enrollment")
        session_enrollment_model = django_apps.get_model("enrollment", "SessionEnrollment")
        exam_model = django_apps.get_model("exams", "Exam")
        self.submission_model = django_apps.get_model("submissions", "Submission")

        self.tenant = tenant_model.objects.create(
            name="ResultsGuardAcademy",
            code="results_guard",
            is_active=True,
        )
        other_tenant = tenant_model.objects.create(
            name="OtherResultsGuardAcademy",
            code="other_results_guard",
            is_active=True,
        )

        user_model = get_user_model()
        self.admin = user_model.objects.create_user(
            username="results_guard_admin",
            password="test1234",
            tenant=self.tenant,
            is_staff=True,
            name="Results Guard Admin",
        )
        membership_model.ensure_active(tenant=self.tenant, user=self.admin, role="owner")

        other_user = user_model.objects.create_user(
            username="other_results_guard_student",
            password="test1234",
            tenant=other_tenant,
            name="Other Student",
        )
        other_student = student_model.objects.create(
            tenant=other_tenant,
            user=other_user,
            ps_number="O001",
            omr_code="0000O001",
            name="Other Student",
            phone="01011110001",
            parent_phone="01022220001",
        )
        membership_model.ensure_active(tenant=other_tenant, user=other_user, role="student")

        other_lecture = lecture_model.objects.create(
            tenant=other_tenant,
            title="OtherMath",
            name="OtherMath",
            subject="MATH",
        )
        other_session = session_model.objects.create(
            lecture=other_lecture,
            order=1,
            title="OtherS1",
        )
        other_enrollment = enrollment_model.objects.create(
            tenant=other_tenant,
            student=other_student,
            lecture=other_lecture,
            status="ACTIVE",
        )
        session_enrollment_model.objects.create(
            tenant=other_tenant,
            session=other_session,
            enrollment=other_enrollment,
        )
        other_exam = exam_model.objects.create(
            tenant=other_tenant,
            title="OtherExam",
            exam_type=exam_model.ExamType.REGULAR,
            pass_score=60,
            max_score=100,
            max_attempts=1,
        )
        other_exam.sessions.add(other_session)

        self.contaminated = self.submission_model.objects.create(
            tenant=self.tenant,
            user=other_user,
            enrollment_id=other_enrollment.id,
            target_type=self.submission_model.TargetType.EXAM,
            target_id=other_exam.id,
            source=self.submission_model.Source.OMR_SCAN,
            status=self.submission_model.Status.SUBMITTED,
            file_key="tenants/results_guard/contaminated.png",
        )

    def test_landing_stats_does_not_leak_cross_tenant_fk_metadata(self):
        view = AdminResultsLandingStatsView.as_view()
        request = self.factory.get("/api/v1/results/admin/landing-stats/")
        force_authenticate(request, user=self.admin)
        request.tenant = self.tenant

        response = view(request)

        self.assertEqual(response.status_code, 200)
        items = {item["id"]: item for item in response.data["pending_top"]}
        self.assertIn(self.contaminated.id, items)
        self.assertEqual(items[self.contaminated.id]["student_name"], "")
        self.assertEqual(items[self.contaminated.id]["target_title"], "")
