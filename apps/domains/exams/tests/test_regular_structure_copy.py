from __future__ import annotations

from django.apps import apps
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.models import Tenant, TenantMembership
from apps.domains.exams.models import (
    AnswerKey,
    Exam,
    ExamAsset,
    ExamQuestion,
    QuestionExplanation,
    Sheet,
)
from apps.domains.exams.services.regular_exam_factory import RegularExamFactory
from apps.domains.exams.views.answer_key_view import AnswerKeyViewSet
from apps.domains.exams.views.exam_structure_view import ExamStructureEnsureView
from apps.domains.exams.views.question_explanation_view import QuestionExplanationDetailView
from apps.domains.exams.views.question_view import QuestionViewSet


User = get_user_model()
Lecture = apps.get_model("lectures", "Lecture")
Session = apps.get_model("lectures", "Session")
ExamResult = apps.get_model("results", "ExamResult")
Result = apps.get_model("results", "Result")
ResultItem = apps.get_model("results", "ResultItem")
OMRDetectedAnswer = apps.get_model("submissions", "OMRDetectedAnswer")
OMRRecognitionRun = apps.get_model("submissions", "OMRRecognitionRun")
Submission = apps.get_model("submissions", "Submission")
SubmissionAnswer = apps.get_model("submissions", "SubmissionAnswer")


class RegularStructureCopyTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.tenant = Tenant.objects.create(
            name="Regular Structure Copy",
            code="regular-structure-copy",
            is_active=True,
        )
        self.admin = User.objects.create_user(
            username="regular_structure_admin",
            password="test1234",
            tenant=self.tenant,
            is_staff=True,
        )
        TenantMembership.ensure_active(tenant=self.tenant, user=self.admin, role="admin")
        self.lecture = Lecture.objects.create(
            tenant=self.tenant,
            title="Structure Lecture",
            name="Structure Lecture",
            subject="SCIENCE",
        )
        self.session = Session.objects.create(
            lecture=self.lecture,
            order=1,
            title="1회",
        )

    def _make_template(self) -> tuple[Exam, list[ExamQuestion]]:
        template = Exam.objects.create(
            tenant=self.tenant,
            title="Source Template",
            exam_type=Exam.ExamType.TEMPLATE,
            subject="SCIENCE",
            max_score=100,
        )
        sheet = Sheet.objects.create(
            exam=template,
            name="MAIN",
            total_questions=2,
            choice_count=1,
            essay_count=1,
            file="exams/sheets/source.pdf",
        )
        q1 = ExamQuestion.objects.create(
            sheet=sheet,
            number=1,
            score=3,
            image_key="questions/q1.png",
            region_meta={"x": 1, "y": 2, "w": 3, "h": 4},
        )
        q2 = ExamQuestion.objects.create(sheet=sheet, number=2, score=30)
        QuestionExplanation.objects.create(
            question=q1,
            text="source explanation",
            image_key="explanations/q1.png",
            source=QuestionExplanation.Source.MANUAL,
        )
        AnswerKey.objects.create(exam=template, answers={str(q1.id): "1", str(q2.id): "해설참조"})
        ExamAsset.objects.create(
            exam=template,
            asset_type=ExamAsset.AssetType.PROBLEM_PDF,
            file_key="assets/problem.pdf",
            file_type="application/pdf",
            file_size=1234,
        )
        return template, [q1, q2]

    def _patch_question_score(self, question: ExamQuestion, score: float):
        request = self.factory.patch(
            f"/api/v1/exams/questions/{question.id}/",
            {"score": score},
            format="json",
        )
        request.tenant = self.tenant
        force_authenticate(request, user=self.admin)
        return QuestionViewSet.as_view({"patch": "partial_update"})(request, pk=question.id)

    def _answer_key_items_for_exam(self, exam: Exam):
        request = self.factory.get(f"/api/v1/exams/answer-keys/?exam={exam.id}")
        request.tenant = self.tenant
        force_authenticate(request, user=self.admin)
        response = AnswerKeyViewSet.as_view({"get": "list"})(request)
        self.assertEqual(response.status_code, 200, response.data)
        return response.data["results"] if isinstance(response.data, dict) else response.data

    def test_regular_from_template_gets_editable_snapshot_without_mutating_template(self):
        template, template_questions = self._make_template()

        regular = RegularExamFactory().create_regular_from_template(
            template_exam=template,
            session_id=self.session.id,
            title="Regular Snapshot",
            tenant=self.tenant,
        )

        regular_questions = list(regular.sheet.questions.order_by("number"))
        self.assertEqual([q.number for q in regular_questions], [1, 2])
        self.assertNotEqual(regular_questions[0].id, template_questions[0].id)
        self.assertEqual(regular_questions[0].region_meta["w"], 3)
        self.assertEqual(regular_questions[0].explanation.text, "source explanation")
        self.assertEqual(regular.assets.get(asset_type=ExamAsset.AssetType.PROBLEM_PDF).file_key, "assets/problem.pdf")

        answers = regular.answer_key.answers
        self.assertEqual(answers[str(regular_questions[0].id)], "1")
        self.assertNotIn(str(template_questions[0].id), answers)

        response = self._patch_question_score(regular_questions[0], 4.5)
        self.assertEqual(response.status_code, 200, response.data)
        regular_questions[0].refresh_from_db()
        template_questions[0].refresh_from_db()
        self.assertEqual(regular_questions[0].score, 4.5)
        self.assertEqual(template_questions[0].score, 3)

        items = self._answer_key_items_for_exam(regular)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["exam"], regular.id)

    def test_ensure_structure_copies_legacy_regular_and_remaps_existing_references(self):
        template, template_questions = self._make_template()
        regular = Exam.objects.create(
            tenant=self.tenant,
            title="Legacy Template Linked",
            exam_type=Exam.ExamType.REGULAR,
            template_exam=template,
        )
        regular.sessions.add(self.session)

        submission = Submission.objects.create(
            tenant=self.tenant,
            user=self.admin,
            target_type=Submission.TargetType.EXAM,
            target_id=regular.id,
            source=Submission.Source.OMR_MANUAL,
            status=Submission.Status.DONE,
        )
        SubmissionAnswer.objects.create(
            tenant=self.tenant,
            submission=submission,
            exam_question_id=template_questions[0].id,
            answer="1",
        )
        run = OMRRecognitionRun.objects.create(
            tenant=self.tenant,
            submission=submission,
            status="done",
        )
        OMRDetectedAnswer.objects.create(
            tenant=self.tenant,
            submission=submission,
            recognition_run=run,
            question_number=1,
            exam_question_id=template_questions[0].id,
            answer="1",
        )
        result = Result.objects.create(
            target_type="exam",
            target_id=regular.id,
            total_score=3,
            max_score=33,
        )
        ResultItem.objects.create(
            result=result,
            question=template_questions[0],
            answer="1",
            is_correct=True,
            score=3,
            max_score=3,
            source="omr",
        )
        ExamResult.objects.create(
            submission=submission,
            exam=regular,
            breakdown={
                "1": {
                    "question_id": template_questions[0].id,
                    "answer": "1",
                    "earned": 3,
                }
            },
        )

        request = self.factory.post(f"/api/v1/exams/{regular.id}/structure/ensure/")
        request.tenant = self.tenant
        force_authenticate(request, user=self.admin)
        response = ExamStructureEnsureView.as_view()(request, exam_id=regular.id)

        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(response.data["structure_copied"])
        regular.refresh_from_db()
        copied_questions = list(regular.sheet.questions.order_by("number"))
        copied_q1 = copied_questions[0]
        self.assertNotEqual(copied_q1.id, template_questions[0].id)

        self.assertEqual(
            SubmissionAnswer.objects.get(submission=submission).exam_question_id,
            copied_q1.id,
        )
        self.assertEqual(
            OMRDetectedAnswer.objects.get(submission=submission).exam_question_id,
            copied_q1.id,
        )
        self.assertEqual(
            ResultItem.objects.get(result=result).question_id,
            copied_q1.id,
        )
        exam_result = ExamResult.objects.get(submission=submission)
        self.assertEqual(exam_result.breakdown["1"]["question_id"], copied_q1.id)

    def test_ensure_structure_claims_regular_owner_when_template_has_no_sheet(self):
        template = Exam.objects.create(
            tenant=self.tenant,
            title="Blank Template",
            exam_type=Exam.ExamType.TEMPLATE,
            subject="SCIENCE",
        )
        ExamAsset.objects.create(
            exam=template,
            asset_type=ExamAsset.AssetType.PROBLEM_PDF,
            file_key="assets/blank.pdf",
            file_type="application/pdf",
            file_size=321,
        )
        regular = Exam.objects.create(
            tenant=self.tenant,
            title="Regular From Blank",
            exam_type=Exam.ExamType.REGULAR,
            template_exam=template,
        )
        regular.sessions.add(self.session)

        request = self.factory.post(f"/api/v1/exams/{regular.id}/structure/ensure/")
        request.tenant = self.tenant
        force_authenticate(request, user=self.admin)
        response = ExamStructureEnsureView.as_view()(request, exam_id=regular.id)

        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(response.data["structure_copied"])
        self.assertEqual(response.data["structure_owner_id"], regular.id)
        self.assertTrue(Sheet.objects.filter(exam=regular, total_questions=0).exists())
        self.assertFalse(Sheet.objects.filter(exam=template).exists())
        self.assertEqual(
            regular.assets.get(asset_type=ExamAsset.AssetType.PROBLEM_PDF).file_key,
            "assets/blank.pdf",
        )

    def test_template_question_explanation_put_is_blocked_for_live_linked_regulars(self):
        template, template_questions = self._make_template()
        regular = Exam.objects.create(
            tenant=self.tenant,
            title="Legacy Template Linked",
            exam_type=Exam.ExamType.REGULAR,
            template_exam=template,
        )
        regular.sessions.add(self.session)

        request = self.factory.put(
            f"/api/v1/exams/questions/{template_questions[0].id}/explanation/",
            {"text": "mutated", "image_key": ""},
            format="json",
        )
        request.tenant = self.tenant
        force_authenticate(request, user=self.admin)
        response = QuestionExplanationDetailView.as_view()(request, question_id=template_questions[0].id)

        self.assertEqual(response.status_code, 400, response.data)
        template_questions[0].explanation.refresh_from_db()
        self.assertEqual(template_questions[0].explanation.text, "source explanation")
