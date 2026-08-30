from unittest.mock import patch

from django.apps import apps as django_apps
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from apps.core.models import Tenant, TenantMembership
from apps.domains.exams.models import Exam, ExamEnrollment


User = get_user_model()


class ExamLectureAssignmentsApiTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(
            name="Exam lecture assignment tenant",
            code="exam-lecture-assignment",
            is_active=True,
        )
        self.admin = User.objects.create_user(
            username="exam_lecture_assignment_admin",
            password="test1234",
            tenant=self.tenant,
            is_staff=True,
            is_superuser=True,
        )
        TenantMembership.ensure_active(
            tenant=self.tenant,
            user=self.admin,
            role="admin",
        )
        self.client = APIClient()
        self.client.force_authenticate(self.admin)
        self.headers = {
            "HTTP_HOST": "localhost",
            "HTTP_X_TENANT_CODE": self.tenant.code,
        }

        self.Lecture = django_apps.get_model("lectures", "Lecture")
        self.Session = django_apps.get_model("lectures", "Session")
        self.Student = django_apps.get_model("students", "Student")
        self.Enrollment = django_apps.get_model("enrollment", "Enrollment")
        self.SessionEnrollment = django_apps.get_model(
            "enrollment",
            "SessionEnrollment",
        )

        self.lecture_a, self.session_a = self._make_lecture_session("A학교 강의")
        self.lecture_b, self.session_b = self._make_lecture_session("B학교 강의")
        self.enrollment_a = self._make_student_enrollment(
            lecture=self.lecture_a,
            session=self.session_a,
            suffix="A01",
        )
        self.enrollment_b = self._make_student_enrollment(
            lecture=self.lecture_b,
            session=self.session_b,
            suffix="B01",
        )
        self.exam = Exam.objects.create(
            tenant=self.tenant,
            title="학교 공통 시험",
            exam_type=Exam.ExamType.REGULAR,
            pass_score=60,
            max_score=100,
        )
        self.exam.sessions.add(self.session_a)
        ExamEnrollment.objects.create(
            exam=self.exam,
            enrollment=self.enrollment_a,
        )

    def _make_lecture_session(self, title):
        lecture = self.Lecture.objects.create(
            tenant=self.tenant,
            title=title,
            name=title,
            subject="MATH",
        )
        session = self.Session.objects.create(
            lecture=lecture,
            order=1,
            title="공통 시험 차시",
        )
        return lecture, session

    def _make_student_enrollment(self, *, lecture, session, suffix):
        user = User.objects.create_user(
            username=f"exam_assignment_student_{suffix}",
            password="test1234",
            tenant=self.tenant,
        )
        student = self.Student.objects.create(
            tenant=self.tenant,
            user=user,
            ps_number=f"PS{suffix}",
            omr_code=f"OMR{suffix}",
            name=f"학생 {suffix}",
            parent_phone="01000000000",
        )
        enrollment = self.Enrollment.objects.create(
            tenant=self.tenant,
            student=student,
            lecture=lecture,
            status="ACTIVE",
        )
        self.SessionEnrollment.objects.create(
            tenant=self.tenant,
            session=session,
            enrollment=enrollment,
        )
        return enrollment

    def _url(self):
        return f"/api/v1/exams/{self.exam.id}/lecture-assignments/"

    def test_attach_session_sets_lecture_cutoff_and_adds_active_roster(self):
        with patch(
            "apps.domains.exams.views.exam_lecture_assignment_view."
            "dispatch_progress_for_exam"
        ) as dispatch:
            with self.captureOnCommitCallbacks(execute=True):
                response = self.client.post(
                    self._url(),
                    {"session_id": self.session_b.id, "pass_score": 70},
                    format="json",
                    **self.headers,
                )

        self.assertEqual(response.status_code, 201, response.data)
        self.assertTrue(self.exam.sessions.filter(id=self.session_b.id).exists())
        self.assertTrue(
            ExamEnrollment.objects.filter(
                exam=self.exam,
                enrollment=self.enrollment_b,
            ).exists()
        )
        self.assertEqual(response.data["total_roster_count"], 2)
        assignment = next(
            row
            for row in response.data["assignments"]
            if row["lecture_id"] == self.lecture_b.id
        )
        self.assertEqual(assignment["pass_score"], 70.0)
        self.assertEqual(assignment["roster_count"], 1)
        self.assertEqual(assignment["selected_count"], 1)
        dispatch.assert_called_once_with(exam_id=self.exam.id)

    def test_patch_updates_one_lecture_without_changing_exam_default(self):
        self.client.post(
            self._url(),
            {"session_id": self.session_b.id, "pass_score": 70},
            format="json",
            **self.headers,
        )

        response = self.client.patch(
            self._url(),
            {"lecture_id": self.lecture_b.id, "pass_score": 75},
            format="json",
            **self.headers,
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.exam.refresh_from_db()
        self.assertEqual(self.exam.pass_score, 60)
        assignment = next(
            row
            for row in response.data["assignments"]
            if row["lecture_id"] == self.lecture_b.id
        )
        self.assertEqual(assignment["pass_score"], 75.0)

    def test_cross_tenant_session_is_rejected_without_link_or_roster_mutation(self):
        other_tenant = Tenant.objects.create(
            name="Other tenant",
            code="exam-lecture-assignment-other",
            is_active=True,
        )
        other_lecture = self.Lecture.objects.create(
            tenant=other_tenant,
            title="다른 학원 강의",
            name="다른 학원 강의",
            subject="MATH",
        )
        other_session = self.Session.objects.create(
            lecture=other_lecture,
            order=1,
            title="다른 학원 차시",
        )

        response = self.client.post(
            self._url(),
            {"session_id": other_session.id, "pass_score": 70},
            format="json",
            **self.headers,
        )

        self.assertEqual(response.status_code, 400, response.data)
        self.assertFalse(self.exam.sessions.filter(id=other_session.id).exists())
