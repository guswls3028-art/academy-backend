from django.contrib.auth import get_user_model
from django.apps import apps as django_apps
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.models import Tenant, TenantMembership
from apps.domains.enrollment.models import Enrollment
from apps.domains.homework.models import HomeworkAssignment
from apps.domains.homework_results.models import Homework, HomeworkScore
from apps.domains.lectures.models import Lecture, Session
from apps.domains.student_app.results.views import MyExamResultView, MyGradesSummaryView
from apps.domains.students.models import Student


User = get_user_model()


class MyGradesSummaryHomeworkTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.tenant = Tenant.objects.create(code="student-grades-hw", name="Student Grades HW", is_active=True)
        self.user = User.objects.create_user(
            username="student-grades-hw-user",
            password="pw1234",
            tenant=self.tenant,
            name="학생",
        )
        TenantMembership.ensure_active(tenant=self.tenant, user=self.user, role="student")
        self.student = Student.objects.create(
            tenant=self.tenant,
            user=self.user,
            ps_number="SGH001",
            omr_code="11112222",
            name="학생",
            phone="01011112222",
            parent_phone="01033334444",
        )
        self.lecture = Lecture.objects.create(
            tenant=self.tenant,
            title="수학",
            name="수학",
            subject="MATH",
        )
        self.session = Session.objects.create(
            lecture=self.lecture,
            order=1,
            title="1회",
        )
        self.enrollment = Enrollment.objects.create(
            tenant=self.tenant,
            student=self.student,
            lecture=self.lecture,
            status="ACTIVE",
        )
        self.Exam = django_apps.get_model("exams", "Exam")
        self.ExamEnrollment = django_apps.get_model("exams", "ExamEnrollment")
        self.Result = django_apps.get_model("results", "Result")

    def _call(self):
        request = self.factory.get("/api/v1/student/grades/")
        request.tenant = self.tenant
        force_authenticate(request, user=self.user)
        return MyGradesSummaryView.as_view()(request)

    def _call_exam_result(self, exam_id: int):
        request = self.factory.get(f"/api/v1/student/results/me/exams/{exam_id}/")
        request.tenant = self.tenant
        force_authenticate(request, user=self.user)
        return MyExamResultView.as_view()(request, exam_id=exam_id)

    def test_assigned_unscored_homework_is_visible_as_not_submitted(self):
        homework = Homework.objects.create(
            tenant=self.tenant,
            session=self.session,
            title="미채점 과제",
            meta={"default_max_score": 20},
        )
        HomeworkAssignment.objects.create(
            tenant=self.tenant,
            homework=homework,
            session=self.session,
            enrollment=self.enrollment,
        )

        response = self._call()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data["homeworks"]), 1)
        row = response.data["homeworks"][0]
        self.assertEqual(row["homework_id"], homework.id)
        self.assertIsNone(row["score"])
        self.assertEqual(row["max_score"], 20.0)
        self.assertEqual(row["achievement"], "NOT_SUBMITTED")
        self.assertEqual(row["lecture_title"], "수학")

    def test_removed_homework_assignment_is_hidden_from_student_summary(self):
        homework = Homework.objects.create(
            tenant=self.tenant,
            session=self.session,
            title="제거된 과제",
            meta={"removed_from_session_at": "2026-05-24T00:00:00+09:00"},
        )
        HomeworkAssignment.objects.create(
            tenant=self.tenant,
            homework=homework,
            session=self.session,
            enrollment=self.enrollment,
        )

        response = self._call()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["homeworks"], [])

    def test_inactive_enrollment_exam_result_is_hidden_from_student_detail(self):
        exam = self.Exam.objects.create(
            tenant=self.tenant,
            title="비활성 수강 시험",
            exam_type=self.Exam.ExamType.REGULAR,
            is_active=True,
            max_score=100,
        )
        exam.sessions.add(self.session)
        self.ExamEnrollment.objects.create(exam=exam, enrollment=self.enrollment)
        self.Result.objects.create(
            target_type="exam",
            target_id=exam.id,
            enrollment=self.enrollment,
            total_score=100,
            max_score=100,
        )
        self.enrollment.status = "INACTIVE"
        self.enrollment.save(update_fields=["status", "updated_at"])

        response = self._call_exam_result(exam.id)

        self.assertEqual(response.status_code, 404)

    def test_inactive_enrollment_scores_are_hidden_from_student_summary(self):
        exam = self.Exam.objects.create(
            tenant=self.tenant,
            title="비활성 수강 성적",
            exam_type=self.Exam.ExamType.REGULAR,
            is_active=True,
            max_score=100,
        )
        exam.sessions.add(self.session)
        self.ExamEnrollment.objects.create(exam=exam, enrollment=self.enrollment)
        self.Result.objects.create(
            target_type="exam",
            target_id=exam.id,
            enrollment=self.enrollment,
            total_score=80,
            max_score=100,
        )
        homework = Homework.objects.create(
            tenant=self.tenant,
            session=self.session,
            title="비활성 수강 과제",
            meta={"default_max_score": 20},
        )
        HomeworkAssignment.objects.create(
            tenant=self.tenant,
            homework=homework,
            session=self.session,
            enrollment=self.enrollment,
        )
        HomeworkScore.objects.create(
            enrollment=self.enrollment,
            session=self.session,
            homework=homework,
            score=18,
            max_score=20,
            passed=True,
        )
        self.enrollment.status = "INACTIVE"
        self.enrollment.save(update_fields=["status", "updated_at"])

        response = self._call()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["exams"], [])
        self.assertEqual(response.data["homeworks"], [])
