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
    WrongNotePDFStaleError,
    _split_tall_image,
    build_wrong_note_pdf,
    build_wrong_note_hwpx,
    generate_and_store_wrong_note_pdf,
)
from apps.domains.results.services.wrong_note_service import (
    WrongNoteQuery,
    build_wrong_note_source_fingerprint,
    list_wrong_notes_for_enrollment,
)
from apps.domains.results.views.wrong_note_view import WrongNoteView

User = get_user_model()
Enrollment = apps.get_model("enrollment", "Enrollment")
AnswerKey = apps.get_model("exams", "AnswerKey")
Exam = apps.get_model("exams", "Exam")
ExamQuestion = apps.get_model("exams", "ExamQuestion")
QuestionExplanation = apps.get_model("exams", "QuestionExplanation")
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

    def test_wrong_note_image_keys_fail_closed_to_current_tenant(self):
        regular, question = self._create_wrong_result(
            title="다른 테넌트 키 차단",
            session=self.session2,
        )
        question.image_key = "tenants/999999/exams/questions/q001.png"
        question.save(update_fields=["image_key", "updated_at"])
        QuestionExplanation.objects.create(
            question=question,
            image_key="tenants/999999/exams/explanations/q001.png",
            source="source_file",
        )

        total, items = list_wrong_notes_for_enrollment(
            enrollment_id=self.enrollment.id,
            q=WrongNoteQuery(exam_id=regular.id, lecture_id=self.lecture.id),
        )

        self.assertEqual(total, 1)
        self.assertEqual(items[0]["question_image_url"], "")
        self.assertEqual(items[0]["explanation_image_url"], "")
        self.assertFalse(items[0]["has_question_image"])
        self.assertFalse(items[0]["has_teacher_explanation"])
        self.assertEqual(items[0]["_question_image_key"], "")
        self.assertEqual(items[0]["_explanation_image_key"], "")

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
        self.assertRegex(response.data["source_fingerprint"], r"^[0-9a-f]{64}$")
        row = response.data["results"][0]
        self.assertEqual(row["exam_title"], "3차시 실전 시험")
        self.assertEqual(row["session_order"], 3)
        self.assertEqual(row["session_title"], "3차시")
        self.assertIn("question_image_url", row)
        self.assertIn("has_question_image", row)
        self.assertNotIn("_question_image_key", row)
        self.assertNotIn("_question_image_name", row)

    def test_source_fingerprint_ignores_presigned_urls_but_tracks_content(self):
        regular, _ = self._create_wrong_result(
            title="fingerprint 시험",
            session=self.session2,
        )
        total, items = list_wrong_notes_for_enrollment(
            enrollment_id=self.enrollment.id,
            q=WrongNoteQuery(exam_id=regular.id, lecture_id=self.lecture.id),
        )
        original = build_wrong_note_source_fingerprint(total=total, items=items)

        items[0]["question_image_url"] = "https://storage.test/refreshed-question"
        items[0]["explanation_image_url"] = "https://storage.test/refreshed-solution"
        self.assertEqual(
            build_wrong_note_source_fingerprint(total=total, items=items),
            original,
        )

        items[0]["student_answer"] = "C"
        self.assertNotEqual(
            build_wrong_note_source_fingerprint(total=total, items=items),
            original,
        )

    def test_legacy_source_fingerprint_stays_rolling_deploy_compatible(self):
        legacy_item = {"exam_id": 5, "question_id": 9}
        legacy_fingerprint = build_wrong_note_source_fingerprint(
            total=1,
            items=[legacy_item],
        )

        self.assertEqual(
            legacy_fingerprint,
            "fc38cd89726bcd06aaf3f31bd83b8e14800697caf6fab9246da51a05de5940b6",
        )
        self.assertNotEqual(
            build_wrong_note_source_fingerprint(
                total=1,
                items=[
                    {
                        **legacy_item,
                        "source_type": "exam",
                        "source_id": 5,
                        "enrollment_id": 42,
                    }
                ],
            ),
            legacy_fingerprint,
        )

    def test_api_source_fingerprint_is_independent_of_page_size(self):
        self._create_wrong_result(
            title="첫 fingerprint 시험",
            session=self.session1,
        )
        self._create_wrong_result(
            title="둘째 fingerprint 시험",
            session=self.session2,
        )
        request = APIRequestFactory().get(
            "/api/v1/results/wrong-notes/",
            {
                "enrollment_id": self.enrollment.id,
                "lecture_id": self.lecture.id,
                "from_session_order": 1,
                "limit": 1,
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
        total, all_items = list_wrong_notes_for_enrollment(
            enrollment_id=self.enrollment.id,
            q=WrongNoteQuery(
                lecture_id=self.lecture.id,
                from_session_order=1,
                limit=200,
            ),
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["count"], 2)
        self.assertEqual(len(response.data["results"]), 1)
        self.assertEqual(
            response.data["source_fingerprint"],
            build_wrong_note_source_fingerprint(total=total, items=all_items),
        )

    @patch(
        "apps.domains.results.services.wrong_note_pdf_service._load_question_image"
    )
    @patch(
        "apps.domains.results.services.wrong_note_pdf_service._load_explanation_image",
        return_value=None,
    )
    def test_pdf_separates_questions_from_solution_section(self, _load_explanation, load_image):
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
            4,
        )

    @patch(
        "apps.domains.results.services.wrong_note_pdf_service._load_explanation_image",
        return_value=None,
    )
    @patch(
        "apps.domains.results.services.wrong_note_pdf_service._load_question_image"
    )
    def test_pdf_packs_two_source_questions_per_problem_page(
        self,
        load_image,
        _load_explanation,
    ):
        from PIL import Image

        for index, session in enumerate(
            (self.session1, self.session2, self.session3),
            start=1,
        ):
            self._create_wrong_result(
                title=f"{index}차시 실전 시험",
                session=session,
                answer="C",
            )
        _, items = list_wrong_notes_for_enrollment(
            enrollment_id=self.enrollment.id,
            q=WrongNoteQuery(lecture_id=self.lecture.id, from_session_order=1),
        )
        load_image.side_effect = lambda *_args, **_kwargs: Image.new(
            "RGB", (640, 480), "white"
        )

        pdf_bytes = build_wrong_note_pdf(
            enrollment=self.enrollment,
            tenant_name=self.tenant.name,
            items=items,
            from_session_order=1,
            exam_id=None,
        )

        self.assertEqual(len(items), 3)
        # cover + ceil(3 / 2) problem pages + divider + 3 solution pages
        self.assertEqual(
            len(re.findall(rb"/Type\s*/Page(?!s)", pdf_bytes)),
            7,
        )
        self.assertEqual(load_image.call_count, 3)

    @patch(
        "apps.domains.results.services.wrong_note_pdf_service._load_explanation_image"
    )
    @patch(
        "apps.domains.results.services.wrong_note_pdf_service._load_question_image"
    )
    def test_hwpx_contains_problem_then_teacher_solution_pages(
        self,
        load_question,
        load_explanation,
    ):
        from io import BytesIO
        from zipfile import ZipFile
        from PIL import Image, ImageDraw

        regular, _ = self._create_wrong_result(
            title="한글 오답 시험",
            session=self.session2,
            answer="C",
        )
        _, items = list_wrong_notes_for_enrollment(
            enrollment_id=self.enrollment.id,
            q=WrongNoteQuery(exam_id=regular.id, lecture_id=self.lecture.id),
        )
        load_question.return_value = Image.new("RGB", (200, 100), "blue")
        explanation = Image.new("RGB", (640, 900), "white")
        ImageDraw.Draw(explanation).rectangle((40, 60, 600, 840), outline="black", width=8)
        load_explanation.return_value = explanation

        hwpx_bytes = build_wrong_note_hwpx(
            enrollment=self.enrollment,
            tenant_name=self.tenant.name,
            items=items,
        )

        with ZipFile(BytesIO(hwpx_bytes)) as package:
            self.assertIn("Contents/content.hpf", package.namelist())
            manifest = package.read("Contents/content.hpf").decode("utf-8")
            preview = package.read("Preview/PrvText.txt").decode("utf-8")
            section_xml = "\n".join(
                package.read(name).decode("utf-8")
                for name in package.namelist()
                if name.startswith("Contents/section") and name.endswith(".xml")
            )
            source_images = [
                name for name in package.namelist() if name.startswith("BinData/")
            ]
        self.assertIn("오답노트", preview)
        self.assertIn("문제 1칸", preview)
        self.assertIn("해설 1쪽", preview)
        self.assertLess(preview.index("문제 1칸"), preview.index("해설 1쪽"))
        self.assertIn("내 풀이 메모", section_xml)
        self.assertIn("정답:", section_xml)
        self.assertIn("추가 메모", section_xml)
        self.assertIn("<hp:pic", section_xml)
        self.assertIn('colCount="2"', section_xml)
        self.assertEqual(len(source_images), 2)
        self.assertIn('id="BIN0001"', manifest)
        self.assertIn('id="BIN0002"', manifest)
        self.assertIn('href="BinData/BIN0002.png"', manifest)

        load_question.return_value = None
        load_explanation.return_value = None
        no_image_hwpx = build_wrong_note_hwpx(
            enrollment=self.enrollment,
            tenant_name=self.tenant.name,
            items=items,
        )
        with ZipFile(BytesIO(no_image_hwpx)) as package:
            no_image_xml = "\n".join(
                package.read(name).decode("utf-8")
                for name in package.namelist()
                if name.startswith("Contents/section") and name.endswith(".xml")
            )
            self.assertFalse(
                any(name.startswith("BinData/") for name in package.namelist())
            )
        self.assertIn("등록된 문제 이미지가 없습니다.", no_image_xml)
        self.assertIn("등록된 선생님 해설 이미지가 없습니다.", no_image_xml)

        load_question.return_value = None
        load_explanation.return_value = None
        load_question.side_effect = lambda *_args, **_kwargs: Image.new(
            "RGB", (200, 100), "blue"
        )
        load_explanation.side_effect = lambda *_args, **_kwargs: Image.new(
            "RGB", (640, 900), "white"
        )
        three_item_hwpx = build_wrong_note_hwpx(
            enrollment=self.enrollment,
            tenant_name=self.tenant.name,
            items=items * 3,
        )
        with ZipFile(BytesIO(three_item_hwpx)) as package:
            three_item_xml = "\n".join(
                package.read(name).decode("utf-8")
                for name in package.namelist()
                if name.startswith("Contents/section") and name.endswith(".xml")
            )
        self.assertEqual(three_item_xml.count('columnBreak="1"'), 2)

    def test_tall_explanation_split_discards_blank_sections(self):
        from PIL import Image, ImageChops, ImageDraw

        source = Image.new("RGB", (500, 2500), "white")
        draw = ImageDraw.Draw(source)
        draw.rectangle((40, 100, 460, 480), fill="black")
        draw.rectangle((40, 1950, 460, 2180), fill="black")

        parts = _split_tall_image(source, max_aspect_ratio=1.2)

        self.assertGreaterEqual(len(parts), 1)
        self.assertLess(sum(part.height for part in parts), source.height // 2)
        for part in parts:
            white = Image.new("RGB", part.size, "white")
            self.assertIsNotNone(ImageChops.difference(part, white).getbbox())
            white.close()
            part.close()
        source.close()

        blank = Image.new("RGB", (500, 2500), "white")
        self.assertEqual(
            _split_tall_image(blank, max_aspect_ratio=1.2),
            [],
        )
        blank.close()

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

    @patch(
        "apps.domains.results.services.wrong_note_pdf_service.upload_fileobj_to_r2_storage"
    )
    @patch("apps.domains.results.services.wrong_note_pdf_service.build_wrong_note_pdf")
    @patch(
        "apps.domains.results.services.wrong_note_pdf_service.list_wrong_notes_for_enrollment"
    )
    def test_pdf_generation_rejects_changed_source_snapshot(
        self,
        list_wrong_notes,
        build_pdf,
        upload_pdf,
    ):
        list_wrong_notes.return_value = (
            1,
            [{"question_id": 7, "student_answer": "B", "extra": {}}],
        )
        job = SimpleNamespace(
            id=92,
            exam_id=None,
            lecture_id=self.lecture.id,
            from_session_order=1,
            to_session_order=3,
            output_format="pdf",
            source_fingerprint="0" * 64,
        )

        with self.assertRaisesRegex(WrongNotePDFStaleError, "변경되었습니다"):
            generate_and_store_wrong_note_pdf(
                job=job,
                enrollment=self.enrollment,
                tenant=self.tenant,
            )

        build_pdf.assert_not_called()
        upload_pdf.assert_not_called()
