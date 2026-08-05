from django.contrib.auth import get_user_model
from django.test import TestCase
from unittest.mock import patch
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.models import Tenant, TenantMembership
from apps.domains.enrollment.models import Enrollment
from apps.domains.exams.models import Exam, ExamQuestion, QuestionExplanation, Sheet
from apps.domains.homework.models import HomeworkAssignment
from apps.domains.homework_results.models import Homework, HomeworkScore
from apps.domains.lectures.models import Lecture, Session
from apps.domains.results.models import Result, ResultItem, WrongNotePDF
from apps.domains.results.services.selected_wrong_note_service import (
    WrongNoteSourceSelectionError,
    list_wrong_notes_for_selection,
)
from apps.domains.students.models import Student
from apps.domains.results.views.wrong_note_pdf_view import WrongNotePDFCreateView


User = get_user_model()


class SelectedWrongNoteSourceTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.tenant = Tenant.objects.create(name="Bundle", code="bundle", is_active=True)
        self.staff = User.objects.create_user(
            username="bundle-admin",
            password="test1234",
            tenant=self.tenant,
            is_staff=True,
        )
        TenantMembership.ensure_active(
            tenant=self.tenant,
            user=self.staff,
            role="admin",
        )
        user = User.objects.create_user(
            username="bundle-student",
            password="test1234",
            tenant=self.tenant,
        )
        self.student = Student.objects.create(
            tenant=self.tenant,
            user=user,
            name="통합 학생",
            ps_number="B001",
            omr_code="20000001",
            parent_phone="01000000002",
        )
        self.lecture1 = Lecture.objects.create(
            tenant=self.tenant,
            title="수학 I",
            name="수학 I",
            subject="MATH",
        )
        self.lecture2 = Lecture.objects.create(
            tenant=self.tenant,
            title="대수",
            name="대수",
            subject="MATH",
        )
        self.session1 = Session.objects.create(lecture=self.lecture1, order=1, title="시험 회차")
        self.session2 = Session.objects.create(lecture=self.lecture2, order=3, title="워크북 회차")
        self.enrollment1 = Enrollment.objects.create(
            tenant=self.tenant,
            student=self.student,
            lecture=self.lecture1,
        )
        self.enrollment2 = Enrollment.objects.create(
            tenant=self.tenant,
            student=self.student,
            lecture=self.lecture2,
        )

    def test_exam_and_workbook_across_lectures_are_bundled(self):
        exam = Exam.objects.create(
            tenant=self.tenant,
            title="수학 I 시험",
            exam_type=Exam.ExamType.REGULAR,
        )
        exam.sessions.add(self.session1, self.session2)
        exam_sheet = Sheet.objects.create(exam=exam, total_questions=1)
        exam_question = ExamQuestion.objects.create(sheet=exam_sheet, number=1)
        result = Result.objects.create(
            enrollment=self.enrollment1,
            target_type="exam",
            target_id=exam.id,
            total_score=0,
            max_score=1,
        )
        ResultItem.objects.create(
            result=result,
            question=exam_question,
            answer="",
            is_correct=False,
            include_in_wrong_note=True,
            score=0,
            max_score=1,
            source="manual",
        )

        source_exam = Exam.objects.create(
            tenant=self.tenant,
            title="대수 워크북 원본",
            exam_type=Exam.ExamType.REGULAR,
            is_active=False,
            segmentation_status=Exam.SegmentationStatus.READY,
        )
        source_sheet = Sheet.objects.create(exam=source_exam, total_questions=1)
        workbook_question = ExamQuestion.objects.create(sheet=source_sheet, number=7)
        QuestionExplanation.objects.create(
            question=workbook_question,
            text="선생님 필기 해설",
            source=QuestionExplanation.Source.SOURCE_FILE,
        )
        homework = Homework.objects.create(
            tenant=self.tenant,
            session=self.session2,
            title="Remake WB 3",
            source_exam=source_exam,
        )
        HomeworkAssignment.objects.create(
            tenant=self.tenant,
            homework=homework,
            session=self.session2,
            enrollment=self.enrollment2,
        )
        HomeworkScore.objects.create(
            enrollment=self.enrollment2,
            session=self.session2,
            homework=homework,
            meta={
                "question_marks": {
                    "7": {"is_correct": True, "include_in_wrong_note": True}
                }
            },
        )

        total, items, normalized = list_wrong_notes_for_selection(
            tenant_id=self.tenant.id,
            student_id=self.student.id,
            source_selection=[
                {"type": "exam", "id": exam.id, "enrollment_id": self.enrollment1.id},
                {"type": "homework", "id": homework.id, "enrollment_id": self.enrollment2.id},
            ],
        )

        self.assertEqual(total, 2)
        self.assertEqual([item["source_type"] for item in items], ["exam", "homework"])
        self.assertEqual(items[0]["session_title"], "시험 회차")
        self.assertEqual(items[1]["exam_title"], "Remake WB 3")
        self.assertTrue(items[1]["is_correct"])
        self.assertTrue(items[1]["include_in_wrong_note"])
        self.assertEqual(items[1]["extra"]["explanation_text"], "선생님 필기 해설")
        self.assertEqual(len(normalized), 2)

        with patch(
            "apps.domains.results.views.wrong_note_pdf_view.publish_wrong_note_pdf_ai_job",
            return_value=True,
        ):
            request = self.factory.post(
                "/api/v1/results/wrong-notes/documents/",
                data={
                    "student_id": self.student.id,
                    "source_selection": normalized,
                    "output_format": "hwpx",
                },
                format="json",
            )
            request.tenant = self.tenant
            force_authenticate(request, user=self.staff)
            response = WrongNotePDFCreateView.as_view()(request)

        self.assertEqual(response.status_code, 202, response.data)
        job = WrongNotePDF.objects.get(id=response.data["job_id"])
        self.assertEqual(job.enrollment_id, self.enrollment1.id)
        self.assertEqual(job.lecture_id, self.enrollment1.lecture_id)
        self.assertIsNone(job.exam_id)
        self.assertEqual(job.source_selection, normalized)
        self.assertEqual(job.output_format, WrongNotePDF.OutputFormat.HWPX)

    def test_selection_rejects_another_students_enrollment(self):
        other_user = User.objects.create_user(
            username="other-student",
            password="test1234",
            tenant=self.tenant,
        )
        other_student = Student.objects.create(
            tenant=self.tenant,
            user=other_user,
            name="다른 학생",
            ps_number="B002",
            omr_code="20000002",
            parent_phone="01000000003",
        )
        other_enrollment = Enrollment.objects.create(
            tenant=self.tenant,
            student=other_student,
            lecture=self.lecture1,
        )
        with self.assertRaises(WrongNoteSourceSelectionError):
            list_wrong_notes_for_selection(
                tenant_id=self.tenant.id,
                student_id=self.student.id,
                source_selection=[
                    {"type": "exam", "id": 1, "enrollment_id": other_enrollment.id}
                ],
            )
