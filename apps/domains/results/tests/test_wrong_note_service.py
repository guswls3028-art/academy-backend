from __future__ import annotations

from django.apps import apps
from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.core.models.tenant import Tenant
from apps.domains.results.models import ResultFact
from apps.domains.results.services.wrong_note_service import (
    WrongNoteQuery,
    list_wrong_notes_for_enrollment,
)

User = get_user_model()
Enrollment = apps.get_model("enrollment", "Enrollment")
AnswerKey = apps.get_model("exams", "AnswerKey")
Exam = apps.get_model("exams", "Exam")
ExamQuestion = apps.get_model("exams", "ExamQuestion")
Sheet = apps.get_model("exams", "Sheet")
Lecture = apps.get_model("lectures", "Lecture")
Session = apps.get_model("lectures", "Session")
Student = apps.get_model("students", "Student")


class WrongNoteServiceSessionExamTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="WrongNoteAcademy", code="wrongnote", is_active=True)
        self.lecture = Lecture.objects.create(
            tenant=self.tenant,
            title="오답노트 강의",
            name="오답노트 강의",
            subject="MATH",
        )
        self.session1 = Session.objects.create(
            lecture=self.lecture,
            order=1,
            title="1차시",
        )
        self.session2 = Session.objects.create(
            lecture=self.lecture,
            order=2,
            title="2차시",
        )
        self.session3 = Session.objects.create(
            lecture=self.lecture,
            order=3,
            title="3차시",
        )
        user = User.objects.create_user(
            username="wrongnote-student",
            password="test1234",
            tenant=self.tenant,
            name="오답노트학생",
        )
        student = Student.objects.create(
            tenant=self.tenant,
            user=user,
            ps_number="WN001",
            omr_code="00000001",
            name="오답노트학생",
            parent_phone="01000000001",
        )
        self.enrollment = Enrollment.objects.create(
            tenant=self.tenant,
            student=student,
            lecture=self.lecture,
            status="ACTIVE",
        )

    def _create_wrong_fact(self, *, title: str, session: Session, answer: str = "B") -> tuple[Exam, ExamQuestion]:
        template = Exam.objects.create(
            tenant=self.tenant,
            title=f"{title} 템플릿",
            exam_type=Exam.ExamType.TEMPLATE,
        )
        sheet = Sheet.objects.create(exam=template, total_questions=1)
        question = ExamQuestion.objects.create(sheet=sheet, number=1, score=5)
        AnswerKey.objects.create(exam=template, answers={str(question.id): answer})

        regular = Exam.objects.create(
            tenant=self.tenant,
            title=title,
            exam_type=Exam.ExamType.REGULAR,
            template_exam=template,
        )
        regular.sessions.add(session)
        ResultFact.objects.create(
            enrollment=self.enrollment,
            target_type="exam",
            target_id=regular.id,
            submission_id=regular.id,
            question_id=question.id,
            answer="A",
            is_correct=False,
            score=0,
            max_score=5,
            source="manual",
            meta={},
        )
        return regular, question

    def test_lecture_order_filter_uses_exam_sessions_m2m(self):
        early_exam, _ = self._create_wrong_fact(title="1차시 시험", session=self.session1)
        included_exam, _ = self._create_wrong_fact(title="2차시 시험", session=self.session2, answer="C")

        total, items = list_wrong_notes_for_enrollment(
            enrollment_id=self.enrollment.id,
            q=WrongNoteQuery(
                lecture_id=self.lecture.id,
                from_session_order=2,
            ),
        )

        self.assertEqual(total, 1)
        self.assertEqual(items[0]["exam_id"], included_exam.id)
        self.assertEqual(items[0]["correct_answer"], "C")
        self.assertNotEqual(items[0]["exam_id"], early_exam.id)

    def test_exam_attached_to_multiple_sessions_is_not_duplicated(self):
        regular, _ = self._create_wrong_fact(title="공유 시험", session=self.session2)
        regular.sessions.add(self.session3)

        total, items = list_wrong_notes_for_enrollment(
            enrollment_id=self.enrollment.id,
            q=WrongNoteQuery(
                lecture_id=self.lecture.id,
                from_session_order=2,
            ),
        )

        self.assertEqual(total, 1)
        self.assertEqual([item["exam_id"] for item in items], [regular.id])
