from __future__ import annotations

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
from apps.domains.student_app.results.views import MyExamResultView, MyGradesSummaryView
from apps.domains.students.models import Student


User = get_user_model()


class StudentScoreReportAnalysisTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.tenant = Tenant.objects.create(code="student-score-report", name="Score Report", is_active=True)
        self.student_user = User.objects.create_user(
            username="score-report-student",
            password="pw1234",
            tenant=self.tenant,
        )
        TenantMembership.ensure_active(tenant=self.tenant, user=self.student_user, role="student")

        self.parent_user = User.objects.create_user(
            username="score-report-parent",
            password="pw1234",
            tenant=self.tenant,
        )
        TenantMembership.ensure_active(tenant=self.tenant, user=self.parent_user, role="parent")
        self.parent = Parent.objects.create(
            tenant=self.tenant,
            user=self.parent_user,
            name="학부모",
            phone="01011112222",
        )

        self.student = Student.objects.create(
            tenant=self.tenant,
            user=self.student_user,
            parent=self.parent,
            ps_number="SR001",
            omr_code="11112222",
            name="분석학생",
            phone="01022223333",
            parent_phone="01011112222",
        )
        self.other_user = User.objects.create_user(
            username="score-report-other-student",
            password="pw1234",
            tenant=self.tenant,
        )
        self.other_student = Student.objects.create(
            tenant=self.tenant,
            user=self.other_user,
            ps_number="SR002",
            omr_code="22223333",
            name="비교학생",
            phone="01033334444",
            parent_phone="01055556666",
        )

        self.lecture = Lecture.objects.create(
            tenant=self.tenant,
            title="대치 수학",
            name="대치 수학",
            subject="MATH",
        )
        self.session = Session.objects.create(lecture=self.lecture, order=1, title="1차시")
        self.enrollment = Enrollment.objects.create(
            tenant=self.tenant,
            student=self.student,
            lecture=self.lecture,
            status="ACTIVE",
        )
        self.other_enrollment = Enrollment.objects.create(
            tenant=self.tenant,
            student=self.other_student,
            lecture=self.lecture,
            status="ACTIVE",
        )
        self.exam = Exam.objects.create(
            tenant=self.tenant,
            title="5월 25일 성적표",
            exam_type=Exam.ExamType.REGULAR,
            pass_score=60,
            max_score=100,
            answer_visibility=Exam.AnswerVisibility.HIDDEN,
        )
        self.exam.sessions.add(self.session)
        ExamEnrollment.objects.create(exam=self.exam, enrollment=self.enrollment)
        ExamEnrollment.objects.create(exam=self.exam, enrollment=self.other_enrollment)
        sheet = Sheet.objects.create(exam=self.exam, name="MAIN", total_questions=3)
        q1 = ExamQuestion.objects.create(sheet=sheet, number=1, score=30)
        q2 = ExamQuestion.objects.create(sheet=sheet, number=2, score=30)
        q3 = ExamQuestion.objects.create(sheet=sheet, number=3, score=40)

        self.result = Result.objects.create(
            target_type="exam",
            target_id=self.exam.id,
            enrollment=self.enrollment,
            total_score=60,
            max_score=100,
            objective_score=60,
            submitted_at=timezone.now(),
        )
        ResultItem.objects.create(
            result=self.result,
            question=q1,
            answer="1",
            is_correct=True,
            score=30,
            max_score=30,
            source="omr",
        )
        ResultItem.objects.create(
            result=self.result,
            question=q2,
            answer="3",
            is_correct=False,
            score=0,
            max_score=30,
            source="omr",
        )
        ResultItem.objects.create(
            result=self.result,
            question=q3,
            answer="2",
            is_correct=False,
            score=30,
            max_score=40,
            source="omr",
        )
        Result.objects.create(
            target_type="exam",
            target_id=self.exam.id,
            enrollment=self.other_enrollment,
            total_score=90,
            max_score=100,
            objective_score=90,
            submitted_at=timezone.now(),
        )

    def _request(self, path: str, *, user=None, student_header: bool = False):
        headers = {}
        if student_header:
            headers["HTTP_X_STUDENT_ID"] = str(self.student.id)
        request = self.factory.get(path, **headers)
        request.tenant = self.tenant
        force_authenticate(request, user=user or self.student_user)
        return request

    def test_exam_result_exposes_wrong_number_analysis_without_correct_answers(self):
        response = MyExamResultView.as_view()(
            self._request(f"/student/results/me/exams/{self.exam.id}/"),
            exam_id=self.exam.id,
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertFalse(response.data["answers_visible"])
        self.assertEqual(response.data["items"][1]["correct_answer"], None)
        self.assertEqual(response.data["analysis"]["total_questions"], 3)
        self.assertEqual(response.data["analysis"]["correct_count"], 1)
        self.assertEqual(response.data["analysis"]["wrong_count"], 2)
        self.assertEqual(response.data["analysis"]["accuracy_rate"], 33.3)
        self.assertEqual(response.data["analysis"]["wrong_question_numbers"], [2, 3])
        self.assertEqual(response.data["rank"], 2)
        self.assertEqual(response.data["cohort_size"], 2)

    def test_grades_summary_includes_wrong_number_analysis(self):
        response = MyGradesSummaryView.as_view()(self._request("/student/grades/"))

        self.assertEqual(response.status_code, 200, response.data)
        row = response.data["exams"][0]
        self.assertEqual(row["exam_id"], self.exam.id)
        self.assertEqual(row["wrong_count"], 2)
        self.assertEqual(row["correct_count"], 1)
        self.assertEqual(row["total_questions"], 3)
        self.assertEqual(row["accuracy_rate"], 33.3)
        self.assertEqual(row["wrong_question_numbers"], [2, 3])

    def test_parent_selected_child_receives_same_score_report_analysis(self):
        response = MyExamResultView.as_view()(
            self._request(
                f"/student/results/me/exams/{self.exam.id}/",
                user=self.parent_user,
                student_header=True,
            ),
            exam_id=self.exam.id,
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["enrollment_id"], self.enrollment.id)
        self.assertEqual(response.data["analysis"]["wrong_question_numbers"], [2, 3])
