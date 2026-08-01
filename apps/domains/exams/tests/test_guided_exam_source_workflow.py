from __future__ import annotations

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.models import Tenant, TenantMembership
from apps.domains.ai.callbacks import _handle_exam_ai_result
from apps.domains.exams.models import (
    Exam,
    ExamAsset,
    ExamQuestion,
    Sheet,
)
from apps.domains.exams.serializers.exam_update import ExamUpdateSerializer
from apps.domains.exams.views.pdf_question_extract_view import (
    PdfQuestionExtractView,
)
from apps.support.exams.view_dependencies import get_exam_ai_job_model


User = get_user_model()
AIJobModel = get_exam_ai_job_model()


class GuidedExamSourceWorkflowTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.tenant = Tenant.objects.create(
            name="Guided Exam",
            code="guided-exam",
            is_active=True,
        )
        self.admin = User.objects.create_user(
            username="guided-exam-admin",
            password="pw1234",
            tenant=self.tenant,
            is_staff=True,
        )
        TenantMembership.ensure_active(
            tenant=self.tenant,
            user=self.admin,
            role="admin",
        )

    def _request(self, exam: Exam, upload: SimpleUploadedFile):
        request = self.factory.post(
            "/api/v1/exams/pdf-extract/",
            {"exam_id": exam.id, "file": upload},
            format="multipart",
        )
        request.tenant = self.tenant
        force_authenticate(request, user=self.admin)
        return request

    @patch(
        "apps.domains.exams.views.pdf_question_extract_view."
        "generate_presigned_download_url",
        return_value="https://files.test/source.pdf",
    )
    @patch(
        "apps.domains.exams.views.pdf_question_extract_view.dispatch_ai_job",
        return_value={"job_id": "guided-job"},
    )
    @patch(
        "apps.domains.exams.views.pdf_question_extract_view."
        "upload_fileobj_to_r2_storage"
    )
    def test_empty_regular_exam_accepts_pdf_and_starts_processing(
        self,
        upload_file,
        dispatch_job,
        presign,
    ):
        exam = Exam.objects.create(
            tenant=self.tenant,
            title="답변형 시험",
            exam_type=Exam.ExamType.REGULAR,
            grading_mode=Exam.GradingMode.WRITTEN,
        )
        upload = SimpleUploadedFile(
            "test.pdf",
            b"%PDF-1.4",
            content_type="application/pdf",
        )

        response = PdfQuestionExtractView.as_view()(
            self._request(exam, upload)
        )

        self.assertEqual(response.status_code, 202, response.data)
        exam.refresh_from_db()
        self.assertEqual(
            exam.segmentation_status,
            Exam.SegmentationStatus.PROCESSING,
        )
        self.assertEqual(exam.source_filename, "test.pdf")
        self.assertTrue(
            ExamAsset.objects.filter(
                exam=exam,
                asset_type=ExamAsset.AssetType.PROBLEM_SOURCE,
            ).exists()
        )
        upload_file.assert_called_once()
        presign.assert_called_once()
        dispatch_job.assert_called_once()

    @patch(
        "apps.domains.exams.views.pdf_question_extract_view.dispatch_ai_job"
    )
    @patch(
        "apps.domains.exams.views.pdf_question_extract_view."
        "upload_fileobj_to_r2_storage"
    )
    def test_hwp_is_preserved_and_requests_faithful_pdf_conversion(
        self,
        upload_file,
        dispatch_job,
    ):
        exam = Exam.objects.create(
            tenant=self.tenant,
            title="HWP 시험",
            exam_type=Exam.ExamType.REGULAR,
            grading_mode=Exam.GradingMode.WRITTEN,
        )
        upload = SimpleUploadedFile(
            "수식시험.hwp",
            b"HWP Document File",
            content_type="application/x-hwp",
        )

        response = PdfQuestionExtractView.as_view()(
            self._request(exam, upload)
        )

        self.assertEqual(response.status_code, 202, response.data)
        self.assertEqual(response.data["status"], "conversion_required")
        exam.refresh_from_db()
        self.assertEqual(
            exam.segmentation_status,
            Exam.SegmentationStatus.CONVERSION_REQUIRED,
        )
        upload_file.assert_called_once()
        dispatch_job.assert_not_called()

    @patch(
        "apps.domains.exams.views.pdf_question_extract_view."
        "upload_fileobj_to_r2_storage"
    )
    def test_regular_exam_with_questions_is_not_overwritten(
        self,
        upload_file,
    ):
        exam = Exam.objects.create(
            tenant=self.tenant,
            title="운영 중 시험",
            exam_type=Exam.ExamType.REGULAR,
        )
        sheet = Sheet.objects.create(exam=exam, total_questions=1)
        ExamQuestion.objects.create(sheet=sheet, number=1)
        upload = SimpleUploadedFile(
            "replacement.pdf",
            b"%PDF-1.4",
            content_type="application/pdf",
        )

        response = PdfQuestionExtractView.as_view()(
            self._request(exam, upload)
        )

        self.assertEqual(response.status_code, 409, response.data)
        upload_file.assert_not_called()
        self.assertEqual(
            ExamQuestion.objects.filter(sheet=sheet).count(),
            1,
        )

    @patch("apps.domains.ai.gateway.dispatch_job")
    def test_callback_builds_mixed_structure_and_distributes_total_score(
        self,
        dispatch_matchup,
    ):
        exam = Exam.objects.create(
            tenant=self.tenant,
            title="혼합형 시험",
            exam_type=Exam.ExamType.REGULAR,
            grading_mode=Exam.GradingMode.MIXED,
            manual_grading_method=Exam.ManualGradingMethod.SCORE,
            choice_question_count=1,
            max_score=100,
            segmentation_status=Exam.SegmentationStatus.PROCESSING,
        )
        AIJobModel.objects.create(
            job_id="guided-callback-job",
            job_type="question_segmentation",
            tenant_id=str(self.tenant.id),
            source_domain="exams",
            source_id=str(exam.id),
        )

        _handle_exam_ai_result(
            job_id="guided-callback-job",
            status="DONE",
            result_payload={
                "exam_id": exam.id,
                "questions": [
                    {
                        "number": 1,
                        "original_number": 1,
                        "bbox": [10, 20, 100, 80],
                        "page_index": 2,
                    },
                    {
                        "number": 2,
                        "original_number": 2,
                        "bbox": [10, 120, 100, 80],
                        "page_index": 2,
                    },
                ],
                "question_image_keys": {
                    "1": f"tenants/{self.tenant.id}/exams/{exam.id}/1.png",
                    "2": f"tenants/{self.tenant.id}/exams/{exam.id}/2.png",
                },
            },
            error=None,
            source_id=str(exam.id),
        )

        exam.refresh_from_db()
        sheet = Sheet.objects.get(exam=exam)
        questions = list(sheet.questions.order_by("number"))
        self.assertEqual(
            exam.segmentation_status,
            Exam.SegmentationStatus.READY,
        )
        self.assertEqual((sheet.choice_count, sheet.essay_count), (1, 1))
        self.assertEqual(
            [question.question_kind for question in questions],
            [
                ExamQuestion.QuestionKind.CHOICE,
                ExamQuestion.QuestionKind.ESSAY,
            ],
        )
        self.assertEqual(
            [float(question.score) for question in questions],
            [50.0, 50.0],
        )
        self.assertEqual(
            [question.image_key for question in questions],
            [
                f"tenants/{self.tenant.id}/exams/{exam.id}/1.png",
                f"tenants/{self.tenant.id}/exams/{exam.id}/2.png",
            ],
        )
        dispatch_matchup.assert_called_once()

    def test_grading_workflow_can_change_after_questions_exist(self):
        exam = Exam.objects.create(
            tenant=self.tenant,
            title="채점 방식 고정 시험",
            exam_type=Exam.ExamType.REGULAR,
            grading_mode=Exam.GradingMode.CHOICE,
            manual_grading_method=Exam.ManualGradingMethod.SCORE,
        )
        sheet = Sheet.objects.create(
            exam=exam,
            total_questions=1,
            choice_count=1,
        )
        question = ExamQuestion.objects.create(
            sheet=sheet,
            number=1,
            question_kind=ExamQuestion.QuestionKind.CHOICE,
        )

        serializer = ExamUpdateSerializer(
            exam,
            data={
                "grading_mode": Exam.GradingMode.WRITTEN,
                "manual_grading_method": Exam.ManualGradingMethod.CORRECTNESS,
            },
            partial=True,
        )

        self.assertTrue(serializer.is_valid(), serializer.errors)
        serializer.save()
        exam.refresh_from_db()
        question.refresh_from_db()
        self.assertEqual(exam.grading_mode, Exam.GradingMode.WRITTEN)
        self.assertEqual(
            exam.manual_grading_method,
            Exam.ManualGradingMethod.CORRECTNESS,
        )
        self.assertEqual(question.sheet_id, sheet.id)
        self.assertEqual(
            question.question_kind,
            ExamQuestion.QuestionKind.CHOICE,
        )

    def test_choice_question_boundary_cannot_change_after_questions_exist(self):
        exam = Exam.objects.create(
            tenant=self.tenant,
            title="혼합형 경계 고정 시험",
            exam_type=Exam.ExamType.REGULAR,
            grading_mode=Exam.GradingMode.MIXED,
            choice_question_count=1,
        )
        Sheet.objects.create(
            exam=exam,
            total_questions=2,
            choice_count=1,
            essay_count=1,
        )

        serializer = ExamUpdateSerializer(
            exam,
            data={"choice_question_count": 2},
            partial=True,
        )

        self.assertFalse(serializer.is_valid())
        self.assertIn("choice_question_count", serializer.errors)
