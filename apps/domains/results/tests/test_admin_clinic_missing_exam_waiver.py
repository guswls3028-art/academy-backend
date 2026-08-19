from django.contrib.auth import get_user_model
from django.apps import apps as django_apps
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.models import Tenant, TenantMembership
from apps.domains.results.models import ExamAttempt, Result
from apps.domains.results.views.admin_clinic_targets_view import (
    AdminClinicMissingExamWaiveView,
    AdminClinicTargetsView,
)


User = get_user_model()


class AdminClinicMissingExamWaiverTests(TestCase):
    def setUp(self):
        self.Enrollment = django_apps.get_model("enrollment", "Enrollment")
        self.SessionEnrollment = django_apps.get_model(
            "enrollment", "SessionEnrollment"
        )
        self.Exam = django_apps.get_model("exams", "Exam")
        self.Lecture = django_apps.get_model("lectures", "Lecture")
        self.Session = django_apps.get_model("lectures", "Session")
        self.ClinicLink = django_apps.get_model("progress", "ClinicLink")
        self.Student = django_apps.get_model("students", "Student")
        self.factory = APIRequestFactory()
        self.tenant = Tenant.objects.create(
            name="Missing exam clinic",
            code="missing-exam-clinic",
            is_active=True,
        )
        self.admin = User.objects.create_user(
            username="missing_exam_admin",
            password="test1234",
            tenant=self.tenant,
            is_staff=True,
        )
        TenantMembership.ensure_active(
            tenant=self.tenant,
            user=self.admin,
            role="admin",
        )
        self.lecture = self.Lecture.objects.create(
            tenant=self.tenant,
            title="과학",
            name="과학",
            subject="SCIENCE",
        )
        self.session = self.Session.objects.create(
            lecture=self.lecture,
            order=1,
            title="결시 확인 차시",
        )
        student_user = User.objects.create_user(
            username="missing_exam_student",
            password="test1234",
            tenant=self.tenant,
        )
        self.student = self.Student.objects.create(
            tenant=self.tenant,
            user=student_user,
            ps_number="MISS001",
            omr_code="MISS001",
            name="결시 학생",
            parent_phone="01000000000",
        )
        self.enrollment = self.Enrollment.objects.create(
            tenant=self.tenant,
            student=self.student,
            lecture=self.lecture,
            status="ACTIVE",
        )
        self.SessionEnrollment.objects.create(
            tenant=self.tenant,
            session=self.session,
            enrollment=self.enrollment,
        )
        self.exam = self.Exam.objects.create(
            tenant=self.tenant,
            title="중간 점검",
            pass_score=60,
            max_score=100,
        )
        self.exam.sessions.add(self.session)
        self.attempt = ExamAttempt.objects.create(
            exam=self.exam,
            enrollment=self.enrollment,
            attempt_index=1,
            status="done",
            meta={"status": "NOT_SUBMITTED"},
        )
        Result.objects.create(
            target_type="exam",
            target_id=self.exam.id,
            enrollment=self.enrollment,
            attempt=self.attempt,
            total_score=0,
            max_score=100,
        )

    def _request(self, method: str, path: str, data=None):
        request = getattr(self.factory, method)(path, data or {}, format="json")
        request.tenant = self.tenant
        force_authenticate(request, user=self.admin)
        return request

    def test_explicit_absence_is_projected_then_waived_with_reason(self):
        before = AdminClinicTargetsView.as_view()(
            self._request("get", "/results/admin/clinic-targets/")
        )
        self.assertEqual(before.status_code, 200, before.data)
        self.assertEqual(len(before.data), 1)
        self.assertEqual(before.data[0]["reason"], "missing")
        self.assertEqual(before.data[0]["meta_status"], "NOT_SUBMITTED")
        self.assertIsNone(before.data[0]["clinic_link_id"])

        payload = {
            "session_id": self.session.id,
            "enrollment_id": self.enrollment.id,
            "exam_id": self.exam.id,
            "memo": "이전 수업 결석으로 면제",
        }
        waived = AdminClinicMissingExamWaiveView.as_view()(
            self._request(
                "post",
                "/results/admin/clinic-targets/waive-missing/",
                payload,
            )
        )
        self.assertEqual(waived.status_code, 201, waived.data)
        link = self.ClinicLink.objects.get(id=waived.data["clinic_link_id"])
        self.assertEqual(link.tenant_id, self.tenant.id)
        self.assertEqual(link.source_type, "exam")
        self.assertEqual(link.source_id, self.exam.id)
        self.assertEqual(
            link.resolution_type,
            self.ClinicLink.ResolutionType.WAIVED,
        )
        self.assertEqual(link.resolution_evidence["memo"], payload["memo"])

        current = AdminClinicTargetsView.as_view()(
            self._request("get", "/results/admin/clinic-targets/")
        )
        self.assertEqual(current.data, [])

        history = AdminClinicTargetsView.as_view()(
            self._request(
                "get",
                "/results/admin/clinic-targets/?include_resolved=true",
            )
        )
        self.assertEqual(len(history.data), 1)
        self.assertEqual(history.data[0]["resolution_type"], "WAIVED")

        repeated = AdminClinicMissingExamWaiveView.as_view()(
            self._request(
                "post",
                "/results/admin/clinic-targets/waive-missing/",
                payload,
            )
        )
        self.assertEqual(repeated.status_code, 200, repeated.data)
        self.assertEqual(self.ClinicLink.objects.count(), 1)

    def test_unmarked_score_cannot_be_waived_as_absence(self):
        self.attempt.meta = {}
        self.attempt.save(update_fields=["meta", "updated_at"])

        response = AdminClinicMissingExamWaiveView.as_view()(
            self._request(
                "post",
                "/results/admin/clinic-targets/waive-missing/",
                {
                    "session_id": self.session.id,
                    "enrollment_id": self.enrollment.id,
                    "exam_id": self.exam.id,
                    "memo": "결석 면제",
                },
            )
        )
        self.assertEqual(response.status_code, 404, response.data)
        self.assertEqual(self.ClinicLink.objects.count(), 0)

    def test_old_absence_does_not_override_a_newer_scored_result(self):
        self.attempt.attempt_index = 2
        self.attempt.meta = {"total_score": 85}
        self.attempt.save(
            update_fields=["attempt_index", "meta", "updated_at"]
        )
        Result.objects.filter(
            target_type="exam",
            target_id=self.exam.id,
            enrollment=self.enrollment,
        ).update(total_score=85, max_score=100)

        current = AdminClinicTargetsView.as_view()(
            self._request("get", "/results/admin/clinic-targets/")
        )
        self.assertEqual(current.status_code, 200, current.data)
        self.assertEqual(current.data, [])

        response = AdminClinicMissingExamWaiveView.as_view()(
            self._request(
                "post",
                "/results/admin/clinic-targets/waive-missing/",
                {
                    "session_id": self.session.id,
                    "enrollment_id": self.enrollment.id,
                    "exam_id": self.exam.id,
                    "memo": "과거 결시",
                },
            )
        )
        self.assertEqual(response.status_code, 404, response.data)
        self.assertEqual(self.ClinicLink.objects.count(), 0)
