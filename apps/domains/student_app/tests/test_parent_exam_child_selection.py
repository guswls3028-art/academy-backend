from __future__ import annotations

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.models import Tenant, TenantMembership
from apps.domains.enrollment.models import Enrollment
from apps.domains.exams.models import Exam, ExamEnrollment, ExamQuestion, Sheet
from apps.domains.lectures.models import Lecture, Session
from apps.domains.parents.models import Parent
from apps.domains.results.models import Result, ResultItem
from apps.domains.student_app.exams.views import StudentExamListView, StudentExamSubmitView
from apps.domains.student_app.results.views import MyExamResultView
from apps.domains.students.models import Student
from apps.domains.submissions.models import Submission


User = get_user_model()


class ParentExamChildSelectionTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.tenant = Tenant.objects.create(code="parent-exams", name="Parent Exams", is_active=True)
        self.parent_user = User.objects.create_user(
            username="parent-exams-parent",
            password="pw1234",
            tenant=self.tenant,
        )
        TenantMembership.ensure_active(tenant=self.tenant, user=self.parent_user, role="parent")
        self.parent = Parent.objects.create(
            tenant=self.tenant,
            user=self.parent_user,
            name="Parent",
            phone="01011112222",
        )
        self.student_a = self._student("a", "A")
        self.student_b = self._student("b", "B")
        self.exam_a, self.enrollment_a, self.question_a = self._exam_for_student(self.student_a, "A Exam")
        self.exam_b, self.enrollment_b, self.question_b = self._exam_for_student(self.student_b, "B Exam")
        self.result_a = Result.objects.create(
            target_type="exam",
            target_id=self.exam_a.id,
            enrollment=self.enrollment_a,
            total_score=10,
            max_score=10,
            objective_score=10,
            submitted_at=timezone.now(),
        )
        ResultItem.objects.create(
            result=self.result_a,
            question=self.question_a,
            answer="1",
            is_correct=True,
            score=10,
            max_score=10,
            source="online",
        )

    def _student(self, suffix: str, name: str) -> Student:
        user = User.objects.create_user(
            username=f"parent-exams-student-{suffix}",
            password="pw1234",
            tenant=self.tenant,
        )
        return Student.objects.create(
            tenant=self.tenant,
            user=user,
            parent=self.parent,
            ps_number=f"PE-{suffix}",
            omr_code=f"PE{suffix.upper()}0000"[:8],
            name=name,
        )

    def _exam_for_student(self, student: Student, title: str):
        lecture = Lecture.objects.create(
            tenant=self.tenant,
            title=f"{title} Lecture",
            name=f"{title} Lecture",
            subject="MATH",
        )
        session = Session.objects.create(lecture=lecture, order=1, title="1회")
        enrollment = Enrollment.objects.create(
            tenant=self.tenant,
            student=student,
            lecture=lecture,
            status="ACTIVE",
        )
        exam = Exam.objects.create(
            tenant=self.tenant,
            title=title,
            exam_type=Exam.ExamType.REGULAR,
            is_active=True,
            pass_score=0,
            max_score=10,
            answer_visibility=Exam.AnswerVisibility.HIDDEN,
        )
        exam.sessions.add(session)
        ExamEnrollment.objects.create(exam=exam, enrollment=enrollment)
        sheet = Sheet.objects.create(exam=exam, name="MAIN", total_questions=1)
        question = ExamQuestion.objects.create(sheet=sheet, number=1, score=10)
        return exam, enrollment, question

    def _request(self, path: str, *, student: Student):
        request = self.factory.get(path, HTTP_X_STUDENT_ID=str(student.id))
        force_authenticate(request, user=self.parent_user)
        request.tenant = self.tenant
        return request

    def _post_request(self, path: str, *, student: Student, data: dict):
        request = self.factory.post(
            path,
            data,
            format="json",
            HTTP_X_STUDENT_ID=str(student.id),
        )
        force_authenticate(request, user=self.parent_user)
        request.tenant = self.tenant
        return request

    def test_parent_exam_list_uses_selected_child(self):
        view = StudentExamListView.as_view()

        response_a = view(self._request("/student/exams/", student=self.student_a))
        response_b = view(self._request("/student/exams/", student=self.student_b))

        self.assertEqual(response_a.status_code, 200)
        self.assertEqual([row["id"] for row in response_a.data["items"]], [self.exam_a.id])
        self.assertEqual(response_b.status_code, 200)
        self.assertEqual([row["id"] for row in response_b.data["items"]], [self.exam_b.id])

    def test_parent_exam_result_uses_selected_child(self):
        view = MyExamResultView.as_view()

        response_a = view(
            self._request(f"/student/results/me/exams/{self.exam_a.id}/", student=self.student_a),
            exam_id=self.exam_a.id,
        )
        response_b = view(
            self._request(f"/student/results/me/exams/{self.exam_a.id}/", student=self.student_b),
            exam_id=self.exam_a.id,
        )

        self.assertEqual(response_a.status_code, 200, response_a.data)
        self.assertEqual(response_a.data["total_score"], 10)
        self.assertEqual(response_b.status_code, 404)

    @patch("apps.domains.submissions.services.dispatcher.dispatch_submission")
    def test_parent_can_submit_same_exam_for_each_selected_child(self, mock_dispatch):
        lecture = Lecture.objects.create(
            tenant=self.tenant,
            title="Shared Lecture",
            name="Shared Lecture",
            subject="MATH",
        )
        session = Session.objects.create(lecture=lecture, order=1, title="1회")
        enrollment_a = Enrollment.objects.create(
            tenant=self.tenant,
            student=self.student_a,
            lecture=lecture,
            status="ACTIVE",
        )
        enrollment_b = Enrollment.objects.create(
            tenant=self.tenant,
            student=self.student_b,
            lecture=lecture,
            status="ACTIVE",
        )
        exam = Exam.objects.create(
            tenant=self.tenant,
            title="Shared Exam",
            exam_type=Exam.ExamType.REGULAR,
            is_active=True,
            pass_score=0,
            max_score=10,
        )
        exam.sessions.add(session)
        ExamEnrollment.objects.create(exam=exam, enrollment=enrollment_a)
        ExamEnrollment.objects.create(exam=exam, enrollment=enrollment_b)
        sheet = Sheet.objects.create(exam=exam, name="MAIN", total_questions=1)
        question = ExamQuestion.objects.create(sheet=sheet, number=1, score=10)
        view = StudentExamSubmitView.as_view()

        response_a = view(
            self._post_request(
                f"/student/exams/{exam.id}/submit/",
                student=self.student_a,
                data={"answers": [{"exam_question_id": question.id, "answer": "1"}]},
            ),
            pk=exam.id,
        )
        response_b = view(
            self._post_request(
                f"/student/exams/{exam.id}/submit/",
                student=self.student_b,
                data={"answers": [{"exam_question_id": question.id, "answer": "2"}]},
            ),
            pk=exam.id,
        )

        self.assertEqual(response_a.status_code, 201, response_a.data)
        self.assertEqual(response_b.status_code, 201, response_b.data)
        submissions = list(
            Submission.objects.filter(target_type=Submission.TargetType.EXAM, target_id=exam.id)
            .order_by("enrollment_id")
        )
        self.assertEqual(len(submissions), 2)
        self.assertEqual(submissions[0].user_id, self.student_a.user_id)
        self.assertEqual(submissions[1].user_id, self.student_b.user_id)
        self.assertEqual(submissions[0].meta["submitted_by_user_id"], self.parent_user.id)
        self.assertEqual(submissions[1].meta["submitted_by_user_id"], self.parent_user.id)
        self.assertEqual(mock_dispatch.call_count, 2)
