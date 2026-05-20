from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.models import Tenant, TenantMembership
from apps.domains.enrollment.models import Enrollment
from apps.domains.exams.models import Exam, ExamEnrollment
from apps.domains.exams.views.exam_view import ExamViewSet
from apps.domains.lectures.models import Lecture, Session
from apps.domains.results.models import ExamAttempt, ExamResult, Result, ResultFact
from apps.domains.students.models import Student
from apps.domains.submissions.models import Submission


User = get_user_model()


class ExamDeleteGuardTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.tenant = Tenant.objects.create(
            name="Exam Delete Guard",
            code="exam-delete-guard",
            is_active=True,
        )
        self.admin = User.objects.create_user(
            username="exam-delete-guard-admin",
            password="pw1234",
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
            title="Delete Guard Lecture",
            name="Delete Guard Lecture",
            subject="MATH",
        )
        student_user = User.objects.create_user(
            username="exam-delete-guard-student",
            password="pw1234",
            tenant=self.tenant,
        )
        self.student = Student.objects.create(
            tenant=self.tenant,
            user=student_user,
            name="Delete Guard Student",
            ps_number="EDG-001",
            omr_code="00000001",
            parent_phone="01000000000",
        )
        self.enrollment = Enrollment.objects.create(
            tenant=self.tenant,
            student=self.student,
            lecture=self.lecture,
            status="ACTIVE",
        )

    def _create_regular_exam(self, suffix: str) -> Exam:
        return Exam.objects.create(
            tenant=self.tenant,
            title=f"Guarded Regular {suffix}",
            exam_type=Exam.ExamType.REGULAR,
            max_score=100,
            pass_score=60,
        )

    def _delete_exam(self, exam: Exam):
        request = self.factory.delete(f"/api/v1/exams/{exam.id}/")
        request.tenant = self.tenant
        force_authenticate(request, user=self.admin)
        return ExamViewSet.as_view({"delete": "destroy"})(request, pk=exam.id)

    def test_empty_regular_exam_remains_deletable(self):
        exam = self._create_regular_exam("empty")

        response = self._delete_exam(exam)

        self.assertEqual(response.status_code, 204)
        self.assertFalse(Exam.objects.filter(id=exam.id).exists())

    def test_regular_exam_with_only_enrollments_remains_deletable(self):
        exam = self._create_regular_exam("enrollments-only")
        ExamEnrollment.objects.create(
            exam=exam,
            enrollment=self.enrollment,
        )

        response = self._delete_exam(exam)

        self.assertEqual(response.status_code, 204)
        self.assertFalse(Exam.objects.filter(id=exam.id).exists())
        self.assertFalse(ExamEnrollment.objects.filter(exam_id=exam.id).exists())

    def test_regular_exam_with_operational_records_cannot_be_deleted(self):
        blockers = (
            (
                "attempt",
                lambda exam: ExamAttempt.objects.create(
                    exam=exam,
                    enrollment=self.enrollment,
                    attempt_index=1,
                ),
            ),
            (
                "submission",
                lambda exam: Submission.objects.create(
                    tenant=self.tenant,
                    user=self.admin,
                    enrollment_id=self.enrollment.id,
                    target_type=Submission.TargetType.EXAM,
                    target_id=exam.id,
                    source=Submission.Source.OMR_SCAN,
                ),
            ),
            (
                "exam result",
                lambda exam: ExamResult.objects.create(
                    submission=Submission.objects.create(
                        tenant=self.tenant,
                        user=self.admin,
                        enrollment_id=self.enrollment.id,
                        target_type=Submission.TargetType.HOMEWORK,
                        target_id=exam.id,
                        source=Submission.Source.HOMEWORK_IMAGE,
                    ),
                    exam=exam,
                ),
            ),
            (
                "result",
                lambda exam: Result.objects.create(
                    target_type="exam",
                    target_id=exam.id,
                    enrollment=self.enrollment,
                    total_score=10,
                    max_score=100,
                ),
            ),
            (
                "result fact",
                lambda exam: ResultFact.objects.create(
                    target_type="exam",
                    target_id=exam.id,
                    enrollment=self.enrollment,
                    submission_id=exam.id,
                    question_id=1,
                    source="manual",
                ),
            ),
        )

        for label, create_child in blockers:
            with self.subTest(label=label):
                exam = self._create_regular_exam(label)
                create_child(exam)

                response = self._delete_exam(exam)

                self.assertEqual(response.status_code, 403, response.data)
                self.assertTrue(Exam.objects.filter(id=exam.id).exists())

    def test_list_filters_by_tenant_exam_type_and_session_id(self):
        other_tenant = Tenant.objects.create(
            name="Other Exam Delete Guard",
            code="exam-delete-guard-other",
            is_active=True,
        )
        lecture_a = self.lecture
        lecture_b = Lecture.objects.create(
            tenant=self.tenant,
            title="Delete Guard Lecture B",
            name="Delete Guard Lecture B",
            subject="MATH",
        )
        other_lecture = Lecture.objects.create(
            tenant=other_tenant,
            title="Other Tenant Lecture",
            name="Other Tenant Lecture",
            subject="MATH",
        )
        session_a = Session.objects.create(
            lecture=lecture_a,
            order=1,
            title="Tenant A Session 1",
        )
        session_b = Session.objects.create(
            lecture=lecture_b,
            order=1,
            title="Tenant A Session 2",
        )
        other_session = Session.objects.create(
            lecture=other_lecture,
            order=1,
            title="Other Tenant Session",
        )

        wanted_exam = self._create_regular_exam("wanted")
        wanted_exam.sessions.add(session_a)

        other_session_exam = self._create_regular_exam("other-session")
        other_session_exam.sessions.add(session_b)

        template_exam = Exam.objects.create(
            tenant=self.tenant,
            title="Template Should Be Filtered",
            exam_type=Exam.ExamType.TEMPLATE,
            max_score=100,
            pass_score=60,
        )
        other_tenant_exam = Exam.objects.create(
            tenant=other_tenant,
            title="Other Tenant Exam",
            exam_type=Exam.ExamType.REGULAR,
            max_score=100,
            pass_score=60,
        )
        other_tenant_exam.sessions.add(other_session)

        request = self.factory.get(
            f"/api/v1/exams/?exam_type=regular&session_id={session_a.id}"
        )
        request.tenant = self.tenant
        force_authenticate(request, user=self.admin)

        response = ExamViewSet.as_view({"get": "list"})(request)

        rows = (
            response.data.get("results", response.data)
            if isinstance(response.data, dict)
            else response.data
        )
        ids = {row["id"] for row in rows}
        self.assertEqual(response.status_code, 200)
        self.assertEqual(ids, {wanted_exam.id})
        self.assertNotIn(other_session_exam.id, ids)
        self.assertNotIn(template_exam.id, ids)
        self.assertNotIn(other_tenant_exam.id, ids)
