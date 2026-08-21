from io import BytesIO
from unittest.mock import patch

from PIL import Image

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.models import Tenant, TenantMembership
from apps.domains.exams.models import (
    AnswerKey,
    Exam,
    ExamQuestion,
    ExamQuestionProposal,
    QuestionExplanation,
    Sheet,
)
from apps.domains.exams.views.segmentation_review_view import (
    ExamSegmentationApproveView,
    ExamSegmentationReviewView,
)


class ExamSegmentationReviewTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.tenant = Tenant.objects.create(name="Review", code="review", is_active=True)
        self.other = Tenant.objects.create(name="Other", code="other", is_active=True)
        self.user = get_user_model().objects.create_user(
            username="review-admin", password="pw", tenant=self.tenant, is_staff=True
        )
        TenantMembership.ensure_active(tenant=self.tenant, user=self.user, role="admin")
        self.exam = Exam.objects.create(
            tenant=self.tenant,
            title="원본 해설 시험",
            exam_type=Exam.ExamType.REGULAR,
            grading_mode=Exam.GradingMode.WRITTEN,
            max_score=100,
            segmentation_status=Exam.SegmentationStatus.REVIEW_REQUIRED,
        )
        self.p1 = ExamQuestionProposal.objects.create(
            exam=self.exam,
            position=1,
            number=1,
            detected_number=1,
            problem_image_key=f"tenants/{self.tenant.id}/exams/questions/{self.exam.id}/q001.png",
            explanation_image_key=f"tenants/{self.tenant.id}/exams/explanations/{self.exam.id}/q001.png",
            region_meta={
                "source_render_mode": "source_content_reconstruction",
                "source_attachment_image_key": (
                    f"tenants/{self.tenant.id}/exams/explanations-review/"
                    f"{self.exam.id}/q001-source-attachment.png"
                ),
                "source_attachment_requires_review": True,
                "answer_candidate": "4",
                "answer_source_image_key": (
                    f"tenants/{self.tenant.id}/exams/answer-sources/"
                    f"{self.exam.id}/page-001.png"
                ),
                "answer_source_requested": True,
                "explanation_source_requested": True,
                "paired_source_status": "partial",
                "source_issues": ["explanation_coverage_incomplete"],
            },
            engine="hwp_endnote",
        )
        self.p2 = ExamQuestionProposal.objects.create(
            exam=self.exam,
            position=2,
            number=2,
            detected_number=2,
            problem_image_key=f"tenants/{self.tenant.id}/exams/questions/{self.exam.id}/q002.png",
            explanation_text="직접 쓴 풀이",
            engine="hwp_endnote",
        )
        # The asset-upload endpoint creates this placeholder before segmentation.
        Sheet.objects.create(exam=self.exam, name="MAIN", total_questions=0)

    def _request(self, method, path, data=None):
        request = getattr(self.factory, method)(path, data or {}, format="json")
        request.tenant = self.tenant
        force_authenticate(request, user=self.user)
        return request

    @patch(
        "apps.domains.exams.views.segmentation_review_view.generate_presigned_get_url_storage",
        side_effect=lambda **kwargs: f"https://files.test/{kwargs['key']}",
    )
    def test_review_lists_problem_and_teacher_explanation(self, _presign):
        response = ExamSegmentationReviewView.as_view()(
            self._request("get", "/review"), exam_id=self.exam.id
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data["items"]), 2)
        self.assertEqual(response.data["paired_source_status"], "partial")
        self.assertEqual(
            response.data["source_issues"],
            ["explanation_coverage_incomplete"],
        )
        self.assertTrue(response.data["answer_source_requested"])
        self.assertTrue(response.data["explanation_source_requested"])
        self.assertTrue(response.data["items"][0]["has_teacher_explanation"])
        self.assertTrue(response.data["items"][0]["crop_adjustable"])
        self.assertEqual(response.data["items"][0]["problem_crop_ratio"], 1.0)
        self.assertIn("q001.png", response.data["items"][0]["explanation_image_url"])
        self.assertIn(
            "q001-source-attachment.png",
            response.data["items"][0]["source_attachment_image_url"],
        )
        self.assertTrue(
            response.data["items"][0]["source_attachment_requires_review"]
        )
        self.assertFalse(response.data["items"][0]["explanation_text_requires_review"])
        self.assertTrue(response.data["items"][1]["explanation_text_requires_review"])
        self.assertEqual(response.data["items"][0]["answer"], "4")
        self.assertIn(
            "answer-sources",
            response.data["items"][0]["answer_source_image_url"],
        )

    @patch("apps.domains.ai.gateway.dispatch_job")
    def test_text_only_ocr_is_not_saved_when_reviewer_does_not_confirm_it(self, _dispatch):
        response = ExamSegmentationApproveView.as_view()(
            self._request(
                "post",
                "/approve",
                {
                    "items": [
                        {"id": self.p1.id, "number": 1, "included": False},
                        {
                            "id": self.p2.id,
                            "number": 2,
                            "included": True,
                            "include_explanation_text": False,
                        },
                    ]
                },
            ),
            exam_id=self.exam.id,
        )

        self.assertEqual(response.status_code, 200, response.data)
        question = ExamQuestion.objects.get(sheet__exam=self.exam)
        self.assertFalse(QuestionExplanation.objects.filter(question=question).exists())

    @patch("apps.domains.ai.gateway.dispatch_job")
    def test_approve_can_renumber_exclude_and_preserves_source_explanation(self, dispatch):
        response = ExamSegmentationApproveView.as_view()(
            self._request(
                "post",
                "/approve",
                {
                    "items": [
                        {"id": self.p1.id, "number": 3, "included": True},
                        {"id": self.p2.id, "number": 2, "included": False},
                    ]
                },
            ),
            exam_id=self.exam.id,
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.exam.refresh_from_db()
        self.assertEqual(self.exam.segmentation_status, Exam.SegmentationStatus.READY)
        question = ExamQuestion.objects.get(sheet__exam=self.exam)
        self.assertEqual(question.sheet.total_questions, 1)
        self.assertEqual(question.number, 3)
        self.assertEqual(question.score, 100)
        explanation = QuestionExplanation.objects.get(question=question)
        self.assertEqual(explanation.source, QuestionExplanation.Source.SOURCE_FILE)
        self.assertTrue(explanation.image_key.endswith("q001.png"))
        self.assertEqual(
            AnswerKey.objects.get(exam=self.exam).answers,
            {str(question.id): "4"},
        )
        self.assertFalse(ExamQuestionProposal.objects.filter(exam=self.exam).exists())
        dispatch.assert_called_once()

    @patch("apps.domains.ai.gateway.dispatch_job")
    def test_approve_saves_teacher_reviewed_answer_by_created_question_id(
        self,
        _dispatch,
    ):
        response = ExamSegmentationApproveView.as_view()(
            self._request(
                "post",
                "/approve",
                {
                    "items": [
                        {
                            "id": self.p1.id,
                            "number": 7,
                            "included": True,
                            "answer": "2|4",
                        },
                        {"id": self.p2.id, "number": 2, "included": False},
                    ]
                },
            ),
            exam_id=self.exam.id,
        )

        self.assertEqual(response.status_code, 200, response.data)
        question = ExamQuestion.objects.get(sheet__exam=self.exam)
        self.assertEqual(question.number, 7)
        self.assertEqual(
            AnswerKey.objects.get(exam=self.exam).answers,
            {str(question.id): "2|4"},
        )

    def test_choice_approval_fails_closed_when_requested_answer_is_missing(self):
        self.exam.grading_mode = Exam.GradingMode.CHOICE
        self.exam.save(update_fields=["grading_mode", "updated_at"])

        response = ExamSegmentationApproveView.as_view()(
            self._request(
                "post",
                "/approve",
                {
                    "items": [
                        {"id": self.p1.id, "number": 1, "included": True},
                        {"id": self.p2.id, "number": 2, "included": True},
                    ]
                },
            ),
            exam_id=self.exam.id,
        )

        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn("2번", str(response.data["items"]))
        self.assertFalse(ExamQuestion.objects.filter(sheet__exam=self.exam).exists())
        self.assertFalse(AnswerKey.objects.filter(exam=self.exam).exists())
        self.exam.refresh_from_db()
        self.assertEqual(
            self.exam.segmentation_status,
            Exam.SegmentationStatus.REVIEW_REQUIRED,
        )

    @patch("apps.domains.ai.gateway.dispatch_job")
    def test_approve_can_explicitly_choose_reviewed_source_attachment(self, _dispatch):
        response = ExamSegmentationApproveView.as_view()(
            self._request(
                "post",
                "/approve",
                {
                    "items": [
                        {
                            "id": self.p1.id,
                            "number": 1,
                            "included": True,
                            "explanation_variant": "source_attachment",
                        },
                        {"id": self.p2.id, "number": 2, "included": False},
                    ]
                },
            ),
            exam_id=self.exam.id,
        )

        self.assertEqual(response.status_code, 200, response.data)
        question = ExamQuestion.objects.get(sheet__exam=self.exam)
        explanation = QuestionExplanation.objects.get(question=question)
        self.assertEqual(
            question.region_meta["explanation_variant"],
            "source_attachment",
        )
        self.assertTrue(explanation.image_key.endswith("q001-source-attachment.png"))

    @patch("apps.domains.ai.gateway.dispatch_job")
    @patch(
        "apps.domains.exams.views.segmentation_review_view."
        "delete_object_r2_storage"
    )
    @patch(
        "apps.domains.exams.views.segmentation_review_view."
        "upload_fileobj_to_r2_storage"
    )
    @patch(
        "apps.domains.exams.views.segmentation_review_view."
        "get_object_bytes_r2_storage"
    )
    def test_approve_recrops_single_hwp_problem_before_canonical_save(
        self,
        get_source,
        upload_crop,
        delete_object,
        _dispatch,
    ):
        source = BytesIO()
        Image.new("RGB", (100, 1000), "white").save(source, format="PNG")
        get_source.return_value = source.getvalue()
        self.p1.problem_crop_ratio = 0.3
        self.p1.save(update_fields=["problem_crop_ratio", "updated_at"])

        response = ExamSegmentationApproveView.as_view()(
            self._request(
                "post",
                "/approve",
                {
                    "items": [
                        {
                            "id": self.p1.id,
                            "number": 1,
                            "included": True,
                            "problem_crop_ratio": 0.62,
                        },
                        {"id": self.p2.id, "number": 2, "included": False},
                    ]
                },
            ),
            exam_id=self.exam.id,
        )

        self.assertEqual(response.status_code, 200, response.data)
        question = ExamQuestion.objects.get(sheet__exam=self.exam)
        self.assertEqual(question.region_meta["problem_crop_ratio"], 0.62)
        self.assertIn("-r6200-", question.image_key)
        self.assertTrue(question.image_key.endswith(".png"))
        upload_crop.assert_called_once()
        uploaded = upload_crop.call_args.kwargs["fileobj"].getvalue()
        with Image.open(BytesIO(uploaded)) as crop:
            self.assertEqual(crop.size, (100, 620))
        delete_object.assert_called_once_with(key=self.p1.problem_image_key)

    def test_other_tenant_exam_fails_closed(self):
        other_exam = Exam.objects.create(
            tenant=self.other,
            title="타 학원",
            exam_type=Exam.ExamType.REGULAR,
            segmentation_status=Exam.SegmentationStatus.REVIEW_REQUIRED,
        )
        response = ExamSegmentationReviewView.as_view()(
            self._request("get", "/review"), exam_id=other_exam.id
        )
        self.assertEqual(response.status_code, 404)

    def test_approve_rejects_non_boolean_included_value(self):
        response = ExamSegmentationApproveView.as_view()(
            self._request(
                "post",
                "/approve",
                {
                    "items": [
                        {"id": self.p1.id, "number": 1, "included": "false"},
                        {"id": self.p2.id, "number": 2, "included": True},
                    ]
                },
            ),
            exam_id=self.exam.id,
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("포함 여부", str(response.data))
