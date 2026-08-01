from __future__ import annotations

import re
from types import SimpleNamespace
from unittest.mock import patch

from django.apps import apps
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.models.tenant import Tenant
from apps.core.models.tenant_membership import TenantMembership
from apps.domains.results.models import Result, ResultFact, ResultItem
from apps.domains.results.services.wrong_note_pdf_service import (
    WrongNotePDFLimitError,
    build_wrong_note_pdf,
    generate_and_store_wrong_note_pdf,
)
from apps.domains.results.services.wrong_note_service import (
    WrongNoteQuery,
    list_wrong_notes_for_enrollment,
)
from apps.domains.results.views.wrong_note_view import WrongNoteView

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
        self.user = User.objects.create_user(
            username="wrongnote-student",
            password="test1234",
            tenant=self.tenant,
            name="오답노트학생",
        )
        TenantMembership.ensure_active(
            tenant=self.tenant,
            user=self.user,
            role="student",
        )
        student = Student.objects.create(
            tenant=self.tenant,
            user=self.user,
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

    def _create_wrong_result(self, *, title: str, session: Session, answer: str = "B") -> tuple[Exam, ExamQuestion]:
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
        result = Result.objects.create(
            enrollment=self.enrollment,
            target_type="exam",
            target_id=regular.id,
            total_score=0,
            max_score=5,
        )
        ResultItem.objects.create(
            result=result,
            question=question,
            answer="A",
            is_correct=False,
            score=0,
            max_score=5,
            source="manual",
        )
        return regular, question

    def test_lecture_order_filter_uses_exam_sessions_m2m(self):
        early_exam, _ = self._create_wrong_result(title="1차시 시험", session=self.session1)
        included_exam, _ = self._create_wrong_result(title="2차시 시험", session=self.session2, answer="C")

        total, items = list_wrong_notes_for_enrollment(
            enrollment_id=self.enrollment.id,
            q=WrongNoteQuery(
                lecture_id=self.lecture.id,
                from_session_order=2,
            ),
        )

        self.assertEqual(total, 1)
        self.assertEqual(items[0]["exam_id"], included_exam.id)
        self.assertEqual(items[0]["exam_title"], "2차시 시험")
        self.assertEqual(items[0]["session_order"], 2)
        self.assertEqual(items[0]["session_title"], "2차시")
        self.assertEqual(items[0]["correct_answer"], "C")
        self.assertNotEqual(items[0]["exam_id"], early_exam.id)

    def test_exam_attached_to_multiple_sessions_is_not_duplicated(self):
        regular, _ = self._create_wrong_result(title="공유 시험", session=self.session2)
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

    def test_lecture_order_filter_applies_inclusive_end(self):
        self._create_wrong_result(title="1차시 시험", session=self.session1)
        included_exam, _ = self._create_wrong_result(
            title="2차시 시험",
            session=self.session2,
        )
        self._create_wrong_result(title="3차시 시험", session=self.session3)

        total, items = list_wrong_notes_for_enrollment(
            enrollment_id=self.enrollment.id,
            q=WrongNoteQuery(
                lecture_id=self.lecture.id,
                from_session_order=2,
                to_session_order=2,
            ),
        )

        self.assertEqual(total, 1)
        self.assertEqual([item["exam_id"] for item in items], [included_exam.id])
        self.assertEqual(items[0]["session_order"], 2)

    def test_lecture_range_uses_regular_order_and_excludes_supplements(self):
        Session.objects.filter(pk=self.session3.pk).update(order=4)
        Session.objects.filter(pk=self.session2.pk).update(order=3)
        supplement = Session.objects.create(
            lecture=self.lecture,
            order=2,
            session_type=Session.SessionType.SUPPLEMENT,
            title="주말 보강",
        )
        supplement_exam, _ = self._create_wrong_result(
            title="보강 시험",
            session=supplement,
        )
        included_exam, _ = self._create_wrong_result(
            title="정규 2차시 시험",
            session=self.session2,
        )

        total, items = list_wrong_notes_for_enrollment(
            enrollment_id=self.enrollment.id,
            q=WrongNoteQuery(
                lecture_id=self.lecture.id,
                from_session_order=2,
                to_session_order=2,
            ),
        )

        self.assertEqual(total, 1)
        self.assertEqual([item["exam_id"] for item in items], [included_exam.id])
        self.assertNotEqual(items[0]["exam_id"], supplement_exam.id)
        self.assertEqual(items[0]["session_order"], 2)
        self.assertEqual(items[0]["session_title"], "2차시")

    def test_multi_session_exam_uses_session_inside_requested_range(self):
        regular, _ = self._create_wrong_result(
            title="공유 범위 시험",
            session=self.session1,
        )
        regular.sessions.add(self.session3)

        total, items = list_wrong_notes_for_enrollment(
            enrollment_id=self.enrollment.id,
            q=WrongNoteQuery(
                lecture_id=self.lecture.id,
                from_session_order=3,
                to_session_order=3,
            ),
        )

        self.assertEqual(total, 1)
        self.assertEqual(items[0]["exam_id"], regular.id)
        self.assertEqual(items[0]["session_order"], 3)
        self.assertEqual(items[0]["session_title"], "3차시")

    def test_append_only_wrong_fact_does_not_override_corrected_snapshot(self):
        regular, question = self._create_wrong_result(title="재채점 시험", session=self.session2)
        result_item = ResultItem.objects.get(
            result__target_id=regular.id,
            question=question,
        )
        result_item.is_correct = True
        result_item.score = 5
        result_item.save(update_fields=["is_correct", "score", "updated_at"])
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

        total, items = list_wrong_notes_for_enrollment(
            enrollment_id=self.enrollment.id,
            q=WrongNoteQuery(
                lecture_id=self.lecture.id,
                from_session_order=1,
            ),
        )

        self.assertEqual(total, 0)
        self.assertEqual(items, [])

    def test_correct_item_marked_for_review_is_included_without_changing_score(self):
        regular, question = self._create_wrong_result(
            title="복습 지정 시험",
            session=self.session2,
        )
        result_item = ResultItem.objects.get(
            result__target_id=regular.id,
            question=question,
        )
        result_item.is_correct = True
        result_item.include_in_wrong_note = True
        result_item.score = 5
        result_item.save(
            update_fields=[
                "is_correct",
                "include_in_wrong_note",
                "score",
                "updated_at",
            ]
        )

        total, items = list_wrong_notes_for_enrollment(
            enrollment_id=self.enrollment.id,
            q=WrongNoteQuery(
                lecture_id=self.lecture.id,
                from_session_order=1,
            ),
        )

        self.assertEqual(total, 1)
        self.assertTrue(items[0]["is_correct"])
        self.assertTrue(items[0]["include_in_wrong_note"])
        self.assertEqual(items[0]["score"], 5.0)

    def test_api_returns_week_and_image_contract_without_storage_keys(self):
        regular, _ = self._create_wrong_result(
            title="3차시 실전 시험",
            session=self.session3,
            answer="C",
        )
        request = APIRequestFactory().get(
            "/api/v1/results/wrong-notes/",
            {
                "enrollment_id": self.enrollment.id,
                "exam_id": regular.id,
                "limit": 200,
            },
        )
        force_authenticate(request, user=self.user)
        request.tenant = self.tenant
        membership = TenantMembership.objects.get(
            tenant=self.tenant,
            user=self.user,
        )
        membership.role = "teacher"
        membership.save(update_fields=["role"])

        response = WrongNoteView.as_view()(request)

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["count"], 1)
        row = response.data["results"][0]
        self.assertEqual(row["exam_title"], "3차시 실전 시험")
        self.assertEqual(row["session_order"], 3)
        self.assertEqual(row["session_title"], "3차시")
        self.assertIn("question_image_url", row)
        self.assertIn("has_question_image", row)
        self.assertNotIn("_question_image_key", row)
        self.assertNotIn("_question_image_name", row)

    @patch(
        "apps.domains.results.services.wrong_note_pdf_service._load_question_image"
    )
    def test_pdf_contains_cover_and_one_page_per_wrong_question(self, load_image):
        from PIL import Image

        regular, question = self._create_wrong_result(
            title="2차시 주간 시험",
            session=self.session2,
            answer="C",
        )
        _, items = list_wrong_notes_for_enrollment(
            enrollment_id=self.enrollment.id,
            q=WrongNoteQuery(
                exam_id=regular.id,
                lecture_id=self.lecture.id,
                from_session_order=1,
            ),
        )
        load_image.return_value = Image.new("RGB", (640, 480), "white")

        pdf_bytes = build_wrong_note_pdf(
            enrollment=self.enrollment,
            tenant_name=self.tenant.name,
            items=items,
            from_session_order=1,
            exam_id=regular.id,
        )

        self.assertTrue(pdf_bytes.startswith(b"%PDF-"))
        self.assertGreater(len(pdf_bytes), 5_000)
        self.assertEqual(
            len(re.findall(rb"/Type\s*/Page(?!s)", pdf_bytes)),
            2,
        )

    @patch(
        "apps.domains.results.services.wrong_note_pdf_service.upload_fileobj_to_r2_storage"
    )
    @patch("apps.domains.results.services.wrong_note_pdf_service.build_wrong_note_pdf")
    @patch(
        "apps.domains.results.services.wrong_note_pdf_service.list_wrong_notes_for_enrollment"
    )
    def test_pdf_generation_rejects_more_than_safe_limit(
        self,
        list_wrong_notes,
        build_pdf,
        upload_pdf,
    ):
        list_wrong_notes.return_value = (
            101,
            [{"question_id": index} for index in range(100)],
        )
        build_pdf.return_value = b"%PDF-complete"
        job = SimpleNamespace(
            id=91,
            exam_id=None,
            lecture_id=self.lecture.id,
            from_session_order=1,
            to_session_order=3,
        )

        with self.assertRaisesRegex(WrongNotePDFLimitError, "101문항"):
            generate_and_store_wrong_note_pdf(
                job=job,
                enrollment=self.enrollment,
                tenant=self.tenant,
            )

        self.assertEqual(list_wrong_notes.call_args.kwargs["q"].limit, 100)
        self.assertEqual(
            list_wrong_notes.call_args.kwargs["q"].to_session_order,
            3,
        )
        build_pdf.assert_not_called()
        upload_pdf.assert_not_called()
