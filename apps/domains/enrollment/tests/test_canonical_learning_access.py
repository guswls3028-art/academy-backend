from __future__ import annotations

from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase

from apps.core.models import Tenant, TenantMembership
from apps.domains.enrollment.models import Enrollment, SessionEnrollment
from apps.domains.exams.models import Exam, ExamEnrollment
from apps.domains.homework.models import HomeworkAssignment, HomeworkEnrollment
from apps.domains.homework_results.models import Homework
from apps.domains.lectures.models import Lecture, Session
from apps.domains.students.models import Student


User = get_user_model()


class StudentLearningAccessCanonicalServiceTests(APITestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Access A", code="access-a", is_active=True)
        self.other_tenant = Tenant.objects.create(name="Access B", code="access-b", is_active=True)
        self.admin = User.objects.create_user(
            username="access-a-admin",
            password="pw1234",
            tenant=self.tenant,
            is_staff=True,
        )
        TenantMembership.ensure_active(tenant=self.tenant, user=self.admin, role="admin")

        self.lecture = Lecture.objects.create(
            tenant=self.tenant,
            title="Lecture A",
            name="Lecture A",
            subject="MATH",
        )
        self.session = Session.objects.create(lecture=self.lecture, order=1, title="A-1")
        self.other_lecture = Lecture.objects.create(
            tenant=self.tenant,
            title="Lecture A2",
            name="Lecture A2",
            subject="MATH",
        )
        self.other_session = Session.objects.create(
            lecture=self.other_lecture,
            order=1,
            title="A2-1",
        )
        self.foreign_lecture = Lecture.objects.create(
            tenant=self.other_tenant,
            title="Lecture B",
            name="Lecture B",
            subject="MATH",
        )
        self.foreign_session = Session.objects.create(
            lecture=self.foreign_lecture,
            order=1,
            title="B-1",
        )

        student_user = User.objects.create_user(
            username="access-a-student",
            password="pw1234",
            tenant=self.tenant,
        )
        self.student = Student.objects.create(
            tenant=self.tenant,
            user=student_user,
            name="Student A",
            ps_number="ACCESS-A-001",
            omr_code="00000001",
            parent_phone="01000000001",
        )
        self.enrollment = Enrollment.objects.create(
            tenant=self.tenant,
            student=self.student,
            lecture=self.lecture,
            status="ACTIVE",
        )
        self.client.force_authenticate(user=self.admin)

    def _headers(self):
        return {"HTTP_HOST": "localhost", "HTTP_X_TENANT_CODE": self.tenant.code}

    def _toggle(self, *, target_type: str, target_id: int, lecture_id: int | None = None):
        return self.client.post(
            f"/api/v1/students/{self.student.id}/enrollment-matrix/toggle/",
            {
                "target_type": target_type,
                "target_id": target_id,
                "lecture_id": lecture_id or self.lecture.id,
                "action": "add",
            },
            format="json",
            **self._headers(),
        )

    def test_session_toggle_rejects_cross_tenant_session(self):
        resp = self._toggle(target_type="session", target_id=self.foreign_session.id)

        self.assertEqual(resp.status_code, 404, resp.data)
        self.assertFalse(
            SessionEnrollment.objects.filter(
                tenant=self.tenant,
                enrollment=self.enrollment,
                session=self.foreign_session,
            ).exists()
        )

    def test_exam_toggle_rejects_same_tenant_other_lecture_exam(self):
        exam = Exam.objects.create(
            tenant=self.tenant,
            title="Other Lecture Exam",
            exam_type=Exam.ExamType.REGULAR,
        )
        exam.sessions.add(self.other_session)

        resp = self._toggle(target_type="exam", target_id=exam.id)

        self.assertEqual(resp.status_code, 404, resp.data)
        self.assertFalse(ExamEnrollment.objects.filter(exam=exam, enrollment=self.enrollment).exists())
        self.assertFalse(
            SessionEnrollment.objects.filter(
                tenant=self.tenant,
                enrollment=self.enrollment,
                session=self.other_session,
            ).exists()
        )

    def test_homework_toggle_rejects_same_tenant_other_lecture_homework(self):
        homework = Homework.objects.create(
            tenant=self.tenant,
            session=self.other_session,
            title="Other Lecture Homework",
        )

        resp = self._toggle(target_type="homework", target_id=homework.id)

        self.assertEqual(resp.status_code, 404, resp.data)
        self.assertFalse(
            HomeworkAssignment.objects.filter(
                tenant=self.tenant,
                homework=homework,
                enrollment=self.enrollment,
            ).exists()
        )
        self.assertFalse(
            SessionEnrollment.objects.filter(
                tenant=self.tenant,
                enrollment=self.enrollment,
                session=self.other_session,
            ).exists()
        )

    def test_valid_exam_toggle_uses_lecture_session_as_canonical_scope(self):
        exam = Exam.objects.create(
            tenant=self.tenant,
            title="Lecture Exam",
            exam_type=Exam.ExamType.REGULAR,
        )
        exam.sessions.add(self.session)

        resp = self._toggle(target_type="exam", target_id=exam.id)

        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertTrue(ExamEnrollment.objects.filter(exam=exam, enrollment=self.enrollment).exists())
        self.assertTrue(
            SessionEnrollment.objects.filter(
                tenant=self.tenant,
                enrollment=self.enrollment,
                session=self.session,
            ).exists()
        )

    def test_exam_enrollment_list_excludes_cross_tenant_corrupted_session_rows(self):
        SessionEnrollment.objects.create(
            tenant=self.tenant,
            session=self.session,
            enrollment=self.enrollment,
        )
        foreign_student_user = User.objects.create_user(
            username="access-b-student",
            password="pw1234",
            tenant=self.other_tenant,
        )
        foreign_student = Student.objects.create(
            tenant=self.other_tenant,
            user=foreign_student_user,
            name="Student B",
            ps_number="ACCESS-B-001",
            omr_code="00000002",
            parent_phone="01000000002",
        )
        foreign_enrollment = Enrollment.objects.create(
            tenant=self.other_tenant,
            student=foreign_student,
            lecture=self.foreign_lecture,
            status="ACTIVE",
        )
        SessionEnrollment.objects.create(
            tenant=self.other_tenant,
            session=self.session,
            enrollment=foreign_enrollment,
        )
        exam = Exam.objects.create(
            tenant=self.tenant,
            title="Scoped Exam",
            exam_type=Exam.ExamType.REGULAR,
        )
        exam.sessions.add(self.session)

        resp = self.client.get(
            f"/api/v1/exams/{exam.id}/enrollments/?session_id={self.session.id}",
            **self._headers(),
        )

        self.assertEqual(resp.status_code, 200, resp.data)
        enrollment_ids = [row["enrollment_id"] for row in resp.data["items"]]
        self.assertEqual(enrollment_ids, [self.enrollment.id])

    def test_homework_enrollment_put_rejects_inactive_session_enrollment(self):
        inactive_user = User.objects.create_user(
            username="access-a-inactive",
            password="pw1234",
            tenant=self.tenant,
        )
        inactive_student = Student.objects.create(
            tenant=self.tenant,
            user=inactive_user,
            name="Inactive Student",
            ps_number="ACCESS-A-002",
            omr_code="00000003",
            parent_phone="01000000003",
        )
        inactive_enrollment = Enrollment.objects.create(
            tenant=self.tenant,
            student=inactive_student,
            lecture=self.lecture,
            status="INACTIVE",
        )
        SessionEnrollment.objects.create(
            tenant=self.tenant,
            session=self.session,
            enrollment=inactive_enrollment,
        )

        resp = self.client.put(
            f"/api/v1/homework/enrollments/?session_id={self.session.id}",
            {"session_id": self.session.id, "enrollment_ids": [inactive_enrollment.id]},
            format="json",
            **self._headers(),
        )

        self.assertEqual(resp.status_code, 400, resp.data)
        self.assertFalse(
            HomeworkEnrollment.objects.filter(
                tenant=self.tenant,
                session=self.session,
                enrollment=inactive_enrollment,
            ).exists()
        )
