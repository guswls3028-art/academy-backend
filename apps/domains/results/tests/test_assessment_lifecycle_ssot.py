import json
from io import StringIO

from django.contrib.auth import get_user_model
from django.apps import apps
from django.core.management import call_command
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.models import Tenant, TenantMembership
from apps.domains.results.utils.session_exam import get_exams_for_session
from apps.domains.results.views.admin_session_exams_summary_view import (
    AdminSessionExamsSummaryView,
)
from apps.domains.results.views.session_scores_view import SessionScoresView


User = get_user_model()


class AssessmentLifecycleSsotTests(TestCase):
    def setUp(self):
        Attendance = apps.get_model("attendance", "Attendance")
        Enrollment = apps.get_model("enrollment", "Enrollment")
        SessionEnrollment = apps.get_model("enrollment", "SessionEnrollment")
        self.Exam = apps.get_model("exams", "Exam")
        Lecture = apps.get_model("lectures", "Lecture")
        Session = apps.get_model("lectures", "Session")
        self.ClinicLink = apps.get_model("progress", "ClinicLink")
        Student = apps.get_model("students", "Student")

        self.factory = APIRequestFactory()
        self.tenant = Tenant.objects.create(
            name="Assessment Lifecycle",
            code="assessment-life",
            is_active=True,
        )
        self.admin = User.objects.create_user(
            username="assessment-life-admin",
            password="test1234",
            tenant=self.tenant,
            is_staff=True,
        )
        TenantMembership.ensure_active(
            tenant=self.tenant,
            user=self.admin,
            role="admin",
        )
        self.lecture = Lecture.objects.create(
            tenant=self.tenant,
            title="Lifecycle Lecture",
            name="Lifecycle Lecture",
            subject="MATH",
        )
        self.session = Session.objects.create(
            lecture=self.lecture,
            order=1,
            title="1회차",
        )
        student_user = User.objects.create_user(
            username="assessment-life-student",
            password="test1234",
            tenant=self.tenant,
        )
        self.student = Student.objects.create(
            tenant=self.tenant,
            user=student_user,
            name="학생",
            ps_number="AL-001",
            omr_code="AL000001",
            parent_phone="01000000000",
        )
        self.enrollment = Enrollment.objects.create(
            tenant=self.tenant,
            student=self.student,
            lecture=self.lecture,
            status="ACTIVE",
        )
        SessionEnrollment.objects.create(
            tenant=self.tenant,
            session=self.session,
            enrollment=self.enrollment,
        )
        Attendance.objects.create(
            tenant=self.tenant,
            session=self.session,
            enrollment=self.enrollment,
            status="PRESENT",
        )

    def _request(self, path: str):
        request = self.factory.get(path)
        request.tenant = self.tenant
        force_authenticate(request, user=self.admin)
        return request

    def test_live_session_exam_ssot_excludes_inactive_and_templates(self):
        active_exam = self.Exam.objects.create(
            tenant=self.tenant,
            title="운영 시험",
            exam_type="regular",
            is_active=True,
        )
        inactive_exam = self.Exam.objects.create(
            tenant=self.tenant,
            title="삭제된 시험",
            exam_type="regular",
            is_active=False,
        )
        template_exam = self.Exam.objects.create(
            tenant=self.tenant,
            title="양식",
            subject="MATH",
            exam_type="template",
            is_active=True,
        )
        active_exam.sessions.add(self.session)
        inactive_exam.sessions.add(self.session)
        template_exam.sessions.add(self.session)

        self.assertEqual(
            list(get_exams_for_session(self.session).values_list("id", flat=True)),
            [active_exam.id],
        )

        response = SessionScoresView.as_view()(
            self._request(f"/api/v1/results/admin/sessions/{self.session.id}/scores/"),
            session_id=self.session.id,
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(
            [row["exam_id"] for row in response.data["meta"]["exams"]],
            [active_exam.id],
        )
        self.assertEqual(
            [row["exam_id"] for row in response.data["rows"][0]["exams"]],
            [active_exam.id],
        )

    def test_summary_clinic_rate_ignores_unresolved_link_whose_source_is_not_live(self):
        SessionProgress = apps.get_model("progress", "SessionProgress")
        inactive_exam = self.Exam.objects.create(
            tenant=self.tenant,
            title="삭제된 시험",
            exam_type="regular",
            is_active=False,
        )
        inactive_exam.sessions.add(self.session)
        SessionProgress.objects.create(
            session=self.session,
            enrollment=self.enrollment,
            completed=False,
        )
        self.ClinicLink.objects.create(
            tenant=self.tenant,
            enrollment=self.enrollment,
            session=self.session,
            reason=self.ClinicLink.Reason.AUTO_FAILED,
            is_auto=True,
            source_type="exam",
            source_id=inactive_exam.id,
        )

        response = AdminSessionExamsSummaryView.as_view()(
            self._request(
                f"/api/v1/results/admin/sessions/{self.session.id}/exams/summary/"
            ),
            session_id=self.session.id,
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["participant_count"], 1)
        self.assertEqual(response.data["clinic_rate"], 0.0)

        active_exam = self.Exam.objects.create(
            tenant=self.tenant,
            title="운영 시험",
            exam_type="regular",
            is_active=True,
        )
        active_exam.sessions.add(self.session)
        self.ClinicLink.objects.create(
            tenant=self.tenant,
            enrollment=self.enrollment,
            session=self.session,
            reason=self.ClinicLink.Reason.AUTO_FAILED,
            is_auto=True,
            source_type="exam",
            source_id=active_exam.id,
        )

        response = AdminSessionExamsSummaryView.as_view()(
            self._request(
                f"/api/v1/results/admin/sessions/{self.session.id}/exams/summary/"
            ),
            session_id=self.session.id,
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["clinic_rate"], 1.0)

    def test_detect_assessment_state_drift_command_reports_non_live_sources(self):
        inactive_exam = self.Exam.objects.create(
            tenant=self.tenant,
            title="삭제된 시험",
            exam_type="regular",
            is_active=False,
        )
        inactive_exam.sessions.add(self.session)
        self.ClinicLink.objects.create(
            tenant=self.tenant,
            enrollment=self.enrollment,
            session=self.session,
            reason=self.ClinicLink.Reason.AUTO_FAILED,
            is_auto=True,
            source_type="exam",
            source_id=inactive_exam.id,
        )

        out = StringIO()
        call_command(
            "detect_assessment_state_drift",
            "--tenant",
            str(self.tenant.id),
            "--json",
            stdout=out,
        )
        report = json.loads(out.getvalue())

        self.assertEqual(report["inactive_regular_linked_exam_count"], 1)
        self.assertEqual(report["unresolved_non_live_source_clinic_link_count"], 1)

    def test_repair_assessment_state_drift_detaches_inactive_exam_links(self):
        inactive_exam = self.Exam.objects.create(
            tenant=self.tenant,
            title="삭제된 시험",
            exam_type="regular",
            is_active=False,
        )
        inactive_exam.sessions.add(self.session)

        out = StringIO()
        call_command(
            "repair_assessment_state_drift",
            "--tenant",
            str(self.tenant.id),
            "--json",
            stdout=out,
        )
        dry_run_report = json.loads(out.getvalue())

        self.assertEqual(dry_run_report["mode"], "dry-run")
        self.assertEqual(dry_run_report["detachable_exam_session_pair_count"], 1)
        self.assertTrue(inactive_exam.sessions.filter(id=self.session.id).exists())

        out = StringIO()
        call_command(
            "repair_assessment_state_drift",
            "--tenant",
            str(self.tenant.id),
            "--apply",
            "--json",
            stdout=out,
        )
        apply_report = json.loads(out.getvalue())
        inactive_exam.refresh_from_db()

        self.assertEqual(apply_report["mode"], "apply")
        self.assertEqual(apply_report["detachable_exam_session_pair_count"], 1)
        self.assertFalse(inactive_exam.sessions.filter(id=self.session.id).exists())
