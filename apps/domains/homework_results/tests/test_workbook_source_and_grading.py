from django.apps import apps
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.models import Tenant, TenantMembership
from apps.domains.homework_results.models import Homework, HomeworkScore
from apps.domains.homework_results.views.homework_view import HomeworkViewSet


User = get_user_model()
Enrollment = apps.get_model("enrollment", "Enrollment")
Exam = apps.get_model("exams", "Exam")
ExamQuestion = apps.get_model("exams", "ExamQuestion")
Sheet = apps.get_model("exams", "Sheet")
HomeworkAssignment = apps.get_model("homework", "HomeworkAssignment")
Lecture = apps.get_model("lectures", "Lecture")
Session = apps.get_model("lectures", "Session")
Student = apps.get_model("students", "Student")


class WorkbookSourceAndGradingTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.tenant = Tenant.objects.create(name="Workbook", code="workbook", is_active=True)
        self.admin = User.objects.create_user(
            username="workbook-admin",
            password="test1234",
            tenant=self.tenant,
            is_staff=True,
        )
        TenantMembership.ensure_active(tenant=self.tenant, user=self.admin, role="admin")
        self.lecture = Lecture.objects.create(
            tenant=self.tenant,
            title="대수",
            name="대수",
            subject="MATH",
        )
        self.session = Session.objects.create(lecture=self.lecture, order=3, title="3회차")
        self.homework = Homework.objects.create(
            tenant=self.tenant,
            session=self.session,
            title="Remake WB 3",
        )
        student_user = User.objects.create_user(
            username="workbook-student",
            password="test1234",
            tenant=self.tenant,
        )
        self.student = Student.objects.create(
            tenant=self.tenant,
            user=student_user,
            name="학생",
            ps_number="WB001",
            omr_code="10000001",
            parent_phone="01000000001",
        )
        self.enrollment = Enrollment.objects.create(
            tenant=self.tenant,
            student=self.student,
            lecture=self.lecture,
        )
        HomeworkAssignment.objects.create(
            tenant=self.tenant,
            homework=self.homework,
            session=self.session,
            enrollment=self.enrollment,
        )

    def _request(self, method: str, path: str, data=None):
        request = getattr(self.factory, method)(path, data=data, format="json")
        request.tenant = self.tenant
        force_authenticate(request, user=self.admin)
        return request

    def test_source_exam_is_hidden_and_idempotent(self):
        view = HomeworkViewSet.as_view({"post": "ensure_source_exam"})
        first = view(
            self._request("post", f"/homeworks/{self.homework.id}/source-exam/"),
            pk=self.homework.id,
        )
        second = view(
            self._request("post", f"/homeworks/{self.homework.id}/source-exam/"),
            pk=self.homework.id,
        )

        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.data["source_exam_id"], second.data["source_exam_id"])
        source_exam = Exam.objects.get(id=first.data["source_exam_id"])
        self.assertFalse(source_exam.is_active)
        self.assertEqual(source_exam.exam_type, Exam.ExamType.REGULAR)
        self.assertEqual(source_exam.sessions.count(), 0)
        self.assertFalse(source_exam.student_results_published)

    def test_question_marks_preserve_score_meta_and_correct_review_item(self):
        source_exam = Exam.objects.create(
            tenant=self.tenant,
            title="워크북 원본",
            exam_type=Exam.ExamType.REGULAR,
            is_active=False,
            segmentation_status=Exam.SegmentationStatus.READY,
        )
        sheet = Sheet.objects.create(exam=source_exam, total_questions=2)
        ExamQuestion.objects.create(sheet=sheet, number=1)
        ExamQuestion.objects.create(sheet=sheet, number=2)
        self.homework.source_exam = source_exam
        self.homework.save(update_fields=["source_exam", "updated_at"])
        score = HomeworkScore.objects.create(
            enrollment=self.enrollment,
            session=self.session,
            homework=self.homework,
            score=80,
            max_score=100,
            meta={"status": "KEEP", "custom": {"a": 1}},
        )

        response = HomeworkViewSet.as_view({"patch": "question_grading"})(
            self._request(
                "patch",
                f"/homeworks/{self.homework.id}/question-grading/",
                {
                    "updates": [
                        {
                            "enrollment_id": self.enrollment.id,
                            "question_number": 1,
                            "is_correct": False,
                            "include_in_wrong_note": True,
                        },
                        {
                            "enrollment_id": self.enrollment.id,
                            "question_number": 2,
                            "is_correct": True,
                            "include_in_wrong_note": True,
                        },
                    ]
                },
            ),
            pk=self.homework.id,
        )

        self.assertEqual(response.status_code, 200)
        score.refresh_from_db()
        self.assertEqual(score.score, 80)
        self.assertEqual(score.meta["status"], "KEEP")
        self.assertEqual(score.meta["custom"], {"a": 1})
        self.assertFalse(score.meta["question_marks"]["1"]["is_correct"])
        self.assertTrue(score.meta["question_marks"]["2"]["is_correct"])
        self.assertTrue(score.meta["question_marks"]["2"]["include_in_wrong_note"])

    def test_question_grading_rejects_unassigned_enrollment(self):
        source_exam = Exam.objects.create(
            tenant=self.tenant,
            title="워크북 원본",
            exam_type=Exam.ExamType.REGULAR,
            is_active=False,
            segmentation_status=Exam.SegmentationStatus.READY,
        )
        sheet = Sheet.objects.create(exam=source_exam, total_questions=1)
        ExamQuestion.objects.create(sheet=sheet, number=1)
        self.homework.source_exam = source_exam
        self.homework.save(update_fields=["source_exam", "updated_at"])

        response = HomeworkViewSet.as_view({"patch": "question_grading"})(
            self._request(
                "patch",
                f"/homeworks/{self.homework.id}/question-grading/",
                {
                    "updates": [
                        {
                            "enrollment_id": self.enrollment.id + 999,
                            "question_number": 1,
                            "is_correct": False,
                            "include_in_wrong_note": True,
                        }
                    ]
                },
            ),
            pk=self.homework.id,
        )
        self.assertEqual(response.status_code, 400)
