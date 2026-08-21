from __future__ import annotations

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.models import Tenant, TenantMembership
from apps.domains.ai.callbacks import _handle_exam_ai_result
from apps.domains.exams.models import (
    AnswerKey,
    Exam,
    ExamAsset,
    ExamQuestion,
    ExamQuestionProposal,
    QuestionExplanation,
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

    def _request(
        self,
        exam: Exam,
        upload: SimpleUploadedFile,
        explanation_upload: SimpleUploadedFile | None = None,
        answer_upload: SimpleUploadedFile | None = None,
    ):
        data = {"exam_id": exam.id, "file": upload}
        if explanation_upload is not None:
            data["explanation_file"] = explanation_upload
        if answer_upload is not None:
            data["answer_file"] = answer_upload
        request = self.factory.post(
            "/api/v1/exams/pdf-extract/",
            data,
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
        self.assertTrue(
            ExamAsset.objects.filter(
                exam=exam,
                asset_type=ExamAsset.AssetType.PROBLEM_PDF,
            ).exists()
        )
        upload_file.assert_called_once()
        presign.assert_called_once()
        dispatch_job.assert_called_once()

    @patch(
        "apps.domains.exams.views.pdf_question_extract_view."
        "generate_presigned_download_url",
        side_effect=[
            "https://files.test/teacher.hwp",
            "https://files.test/problems.pdf",
        ],
    )
    @patch(
        "apps.domains.exams.views.pdf_question_extract_view.dispatch_ai_job",
        return_value={"job_id": "paired-job"},
    )
    @patch(
        "apps.domains.exams.views.pdf_question_extract_view."
        "upload_fileobj_to_r2_storage"
    )
    def test_clean_problem_pdf_can_pair_teacher_hwp_by_number(
        self,
        upload_file,
        dispatch_job,
        _presign,
    ):
        exam = Exam.objects.create(
            tenant=self.tenant,
            title="Ymath 짝 자료 시험",
            exam_type=Exam.ExamType.REGULAR,
            grading_mode=Exam.GradingMode.WRITTEN,
        )
        problem_upload = SimpleUploadedFile(
            "문제지.pdf",
            b"%PDF-1.4",
            content_type="application/pdf",
        )
        explanation_upload = SimpleUploadedFile(
            "선생님 해설.hwp",
            b"HWP Document File",
            content_type="application/x-hwp",
        )

        response = PdfQuestionExtractView.as_view()(
            self._request(exam, problem_upload, explanation_upload)
        )

        self.assertEqual(response.status_code, 202, response.data)
        self.assertEqual(upload_file.call_count, 2)
        self.assertTrue(
            ExamAsset.objects.filter(
                exam=exam,
                asset_type=(
                    ExamAsset.AssetType.TEACHER_EXPLANATION_SOURCE
                ),
            ).exists()
        )
        payload = dispatch_job.call_args.kwargs["payload"]
        self.assertEqual(payload["filename"], "문제지.pdf")
        self.assertEqual(payload["explanation_filename"], "선생님 해설.hwp")
        self.assertEqual(
            payload["explanation_download_url"],
            "https://files.test/teacher.hwp",
        )

    @patch(
        "apps.domains.exams.views.pdf_question_extract_view."
        "generate_presigned_download_url",
        side_effect=[
            "https://files.test/explanations.png",
            "https://files.test/answers.pdf",
            "https://files.test/problems.pdf",
        ],
    )
    @patch(
        "apps.domains.exams.views.pdf_question_extract_view.dispatch_ai_job",
        return_value={"job_id": "three-source-job"},
    )
    @patch(
        "apps.domains.exams.views.pdf_question_extract_view."
        "upload_fileobj_to_r2_storage"
    )
    def test_problem_answer_and_explanation_roles_are_preserved_and_dispatched(
        self,
        upload_file,
        dispatch_job,
        _presign,
    ):
        exam = Exam.objects.create(
            tenant=self.tenant,
            title="세 파일 역할 시험",
            exam_type=Exam.ExamType.REGULAR,
            grading_mode=Exam.GradingMode.MIXED,
        )
        response = PdfQuestionExtractView.as_view()(
            self._request(
                exam,
                SimpleUploadedFile(
                    "문제지.pdf",
                    b"%PDF-1.4 problems",
                    content_type="application/pdf",
                ),
                SimpleUploadedFile(
                    "해설지.png",
                    b"png-explanation-fixture",
                    content_type="image/png",
                ),
                SimpleUploadedFile(
                    "정답지.pdf",
                    b"%PDF-1.4 answers",
                    content_type="application/pdf",
                ),
            )
        )

        self.assertEqual(response.status_code, 202, response.data)
        self.assertEqual(upload_file.call_count, 3)
        self.assertTrue(
            ExamAsset.objects.filter(
                exam=exam,
                asset_type=ExamAsset.AssetType.ANSWER_SOURCE,
            ).exists()
        )
        self.assertTrue(
            ExamAsset.objects.filter(
                exam=exam,
                asset_type=ExamAsset.AssetType.TEACHER_EXPLANATION_SOURCE,
            ).exists()
        )
        payload = dispatch_job.call_args.kwargs["payload"]
        self.assertEqual(payload["filename"], "문제지.pdf")
        self.assertEqual(payload["answer_filename"], "정답지.pdf")
        self.assertEqual(payload["explanation_filename"], "해설지.png")
        self.assertEqual(
            payload["answer_download_url"],
            "https://files.test/answers.pdf",
        )
        self.assertEqual(
            payload["explanation_download_url"],
            "https://files.test/explanations.png",
        )
        self.assertTrue(payload["answer_source_requested"])
        self.assertTrue(payload["explanation_source_requested"])

    @patch(
        "apps.domains.exams.views.pdf_question_extract_view."
        "delete_object_r2_storage"
    )
    @patch(
        "apps.domains.exams.views.pdf_question_extract_view."
        "upload_fileobj_to_r2_storage",
        side_effect=[None, RuntimeError("answer upload failed")],
    )
    def test_partial_multi_source_upload_deletes_only_unreferenced_objects(
        self,
        upload_file,
        delete_object,
    ):
        exam = Exam.objects.create(
            tenant=self.tenant,
            title="부분 업로드 실패 시험",
            exam_type=Exam.ExamType.REGULAR,
        )

        response = PdfQuestionExtractView.as_view()(
            self._request(
                exam,
                SimpleUploadedFile(
                    "문제지.pdf",
                    b"%PDF-1.4 problems",
                    content_type="application/pdf",
                ),
                answer_upload=SimpleUploadedFile(
                    "정답지.pdf",
                    b"%PDF-1.4 answers",
                    content_type="application/pdf",
                ),
            )
        )

        self.assertEqual(response.status_code, 500, response.data)
        exam.refresh_from_db()
        self.assertEqual(exam.segmentation_status, Exam.SegmentationStatus.FAILED)
        self.assertFalse(ExamAsset.objects.filter(exam=exam).exists())
        first_uploaded_key = upload_file.call_args_list[0].kwargs["key"]
        delete_object.assert_called_once_with(key=first_uploaded_key)

    @patch(
        "apps.domains.exams.views.pdf_question_extract_view."
        "delete_object_r2_storage"
    )
    @patch(
        "apps.domains.exams.views.pdf_question_extract_view."
        "generate_presigned_download_url",
        side_effect=[
            "https://files.test/explanation-old.pdf",
            "https://files.test/answer-old.pdf",
            "https://files.test/problem-new.pdf",
        ],
    )
    @patch(
        "apps.domains.exams.views.pdf_question_extract_view.dispatch_ai_job",
        return_value={"job_id": "reprocess-job"},
    )
    @patch(
        "apps.domains.exams.views.pdf_question_extract_view."
        "upload_fileobj_to_r2_storage"
    )
    def test_reprocess_replaces_assets_and_deletes_unreferenced_old_original(
        self,
        _upload_file,
        dispatch_job,
        _presign,
        delete_object,
    ):
        exam = Exam.objects.create(
            tenant=self.tenant,
            title="재처리 시험",
            exam_type=Exam.ExamType.REGULAR,
            segmentation_status=Exam.SegmentationStatus.REVIEW_REQUIRED,
        )
        old_key = f"tenants/{self.tenant.id}/exams/pdf-extract/old/source.pdf"
        ExamAsset.objects.create(
            exam=exam,
            asset_type=ExamAsset.AssetType.PROBLEM_SOURCE,
            file_key=old_key,
            file_type="application/pdf",
            file_size=10,
        )
        old_answer_key = (
            f"tenants/{self.tenant.id}/exams/pdf-extract/old/answers.pdf"
        )
        old_explanation_key = (
            f"tenants/{self.tenant.id}/exams/pdf-extract/old/explanations.pdf"
        )
        ExamAsset.objects.create(
            exam=exam,
            asset_type=ExamAsset.AssetType.ANSWER_SOURCE,
            file_key=old_answer_key,
            file_type="application/pdf",
            file_size=20,
        )
        ExamAsset.objects.create(
            exam=exam,
            asset_type=ExamAsset.AssetType.TEACHER_EXPLANATION_SOURCE,
            file_key=old_explanation_key,
            file_type="application/pdf",
            file_size=30,
        )

        response = PdfQuestionExtractView.as_view()(
            self._request(
                exam,
                SimpleUploadedFile(
                    "새문제지.pdf",
                    b"%PDF-1.4 new",
                    content_type="application/pdf",
                ),
            )
        )

        self.assertEqual(response.status_code, 202, response.data)
        new_key = ExamAsset.objects.get(
            exam=exam,
            asset_type=ExamAsset.AssetType.PROBLEM_SOURCE,
        ).file_key
        self.assertNotEqual(new_key, old_key)
        delete_object.assert_called_once_with(key=old_key)
        payload = dispatch_job.call_args.kwargs["payload"]
        self.assertTrue(payload["answer_source_requested"])
        self.assertTrue(payload["explanation_source_requested"])
        self.assertEqual(
            payload["answer_download_url"],
            "https://files.test/answer-old.pdf",
        )
        self.assertEqual(
            payload["explanation_download_url"],
            "https://files.test/explanation-old.pdf",
        )
        self.assertEqual(
            ExamAsset.objects.get(
                exam=exam,
                asset_type=ExamAsset.AssetType.ANSWER_SOURCE,
            ).file_key,
            old_answer_key,
        )
        self.assertEqual(
            ExamAsset.objects.get(
                exam=exam,
                asset_type=ExamAsset.AssetType.TEACHER_EXPLANATION_SOURCE,
            ).file_key,
            old_explanation_key,
        )

    @patch(
        "apps.domains.exams.views.pdf_question_extract_view."
        "generate_presigned_download_url",
        return_value="https://files.test/source.hwp",
    )
    @patch(
        "apps.domains.exams.views.pdf_question_extract_view.dispatch_ai_job",
        return_value={"job_id": "hwp-job"},
    )
    @patch(
        "apps.domains.exams.views.pdf_question_extract_view."
        "upload_fileobj_to_r2_storage"
    )
    def test_hwp_is_preserved_and_dispatched_for_endnote_extraction(
        self,
        upload_file,
        dispatch_job,
        presign,
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
        self.assertEqual(response.data["status"], "submitted")
        exam.refresh_from_db()
        self.assertEqual(
            exam.segmentation_status,
            Exam.SegmentationStatus.PROCESSING,
        )
        upload_file.assert_called_once()
        presign.assert_called_once()
        dispatch_job.assert_called_once()

    @patch(
        "apps.domains.exams.views.pdf_question_extract_view."
        "generate_presigned_download_url",
        return_value="https://files.test/source.hwpx",
    )
    @patch(
        "apps.domains.exams.views.pdf_question_extract_view.dispatch_ai_job",
        return_value={"job_id": "hwpx-job"},
    )
    @patch(
        "apps.domains.exams.views.pdf_question_extract_view."
        "upload_fileobj_to_r2_storage"
    )
    def test_hwpx_is_dispatched_instead_of_forcing_manual_conversion(
        self,
        upload_file,
        dispatch_job,
        presign,
    ):
        exam = Exam.objects.create(
            tenant=self.tenant,
            title="HWPX 시험",
            exam_type=Exam.ExamType.REGULAR,
            grading_mode=Exam.GradingMode.WRITTEN,
        )
        upload = SimpleUploadedFile(
            "미주해설.hwpx",
            b"PK HWPX fixture",
            content_type="application/hwp+zip",
        )

        response = PdfQuestionExtractView.as_view()(self._request(exam, upload))

        self.assertEqual(response.status_code, 202, response.data)
        self.assertEqual(response.data["status"], "submitted")
        exam.refresh_from_db()
        self.assertEqual(
            exam.segmentation_status,
            Exam.SegmentationStatus.PROCESSING,
        )
        upload_file.assert_called_once()
        presign.assert_called_once()
        payload = dispatch_job.call_args.kwargs["payload"]
        self.assertEqual(payload["filename"], "미주해설.hwpx")

    @patch(
        "apps.domains.exams.views.pdf_question_extract_view."
        "generate_presigned_download_url"
    )
    @patch(
        "apps.domains.exams.views.pdf_question_extract_view.dispatch_ai_job"
    )
    @patch(
        "apps.domains.exams.views.pdf_question_extract_view."
        "upload_fileobj_to_r2_storage"
    )
    def test_office_source_is_preserved_without_forcing_pdf(
        self,
        upload_file,
        dispatch_job,
        presign,
    ):
        exam = Exam.objects.create(
            tenant=self.tenant,
            title="DOCX 원본 시험",
            exam_type=Exam.ExamType.REGULAR,
            grading_mode=Exam.GradingMode.WRITTEN,
        )
        upload = SimpleUploadedFile(
            "수학문제.docx",
            b"PK Office fixture",
            content_type=(
                "application/vnd.openxmlformats-officedocument."
                "wordprocessingml.document"
            ),
        )

        response = PdfQuestionExtractView.as_view()(
            self._request(exam, upload)
        )

        self.assertEqual(response.status_code, 202, response.data)
        self.assertEqual(response.data["status"], "source_saved")
        self.assertFalse(response.data["processing_started"])
        self.assertIn(
            "PDF 재업로드는 필수가 아닙니다",
            response.data["message"],
        )
        exam.refresh_from_db()
        self.assertEqual(
            exam.segmentation_status,
            Exam.SegmentationStatus.CONVERSION_REQUIRED,
        )
        self.assertEqual(exam.source_filename, "수학문제.docx")
        source_asset = ExamAsset.objects.get(
            exam=exam,
            asset_type=ExamAsset.AssetType.PROBLEM_SOURCE,
        )
        self.assertEqual(source_asset.file_type, upload.content_type)
        upload_file.assert_called_once()
        self.assertEqual(
            upload_file.call_args.kwargs["content_type"],
            "application/octet-stream",
        )
        presign.assert_not_called()
        dispatch_job.assert_not_called()

    @patch(
        "apps.domains.exams.views.pdf_question_extract_view."
        "generate_presigned_download_url",
        return_value="https://files.test/problems.pdf",
    )
    @patch(
        "apps.domains.exams.views.pdf_question_extract_view.dispatch_ai_job",
        return_value={"job_id": "problem-only-job"},
    )
    @patch(
        "apps.domains.exams.views.pdf_question_extract_view."
        "upload_fileobj_to_r2_storage"
    )
    def test_any_safe_explanation_format_is_preserved_while_problem_runs(
        self,
        upload_file,
        dispatch_job,
        presign,
    ):
        exam = Exam.objects.create(
            tenant=self.tenant,
            title="PPTX 해설 시험",
            exam_type=Exam.ExamType.REGULAR,
            grading_mode=Exam.GradingMode.WRITTEN,
        )
        problem_upload = SimpleUploadedFile(
            "문제지.pdf",
            b"%PDF-1.4",
            content_type="application/pdf",
        )
        explanation_upload = SimpleUploadedFile(
            "선생님해설.pptx",
            b"PK Office explanation fixture",
            content_type=(
                "application/vnd.openxmlformats-officedocument."
                "presentationml.presentation"
            ),
        )

        response = PdfQuestionExtractView.as_view()(
            self._request(exam, problem_upload, explanation_upload)
        )

        self.assertEqual(response.status_code, 202, response.data)
        self.assertEqual(response.data["status"], "submitted")
        self.assertTrue(response.data["processing_started"])
        self.assertEqual(upload_file.call_count, 2)
        self.assertEqual(presign.call_count, 1)
        payload = dispatch_job.call_args.kwargs["payload"]
        self.assertEqual(payload["filename"], "문제지.pdf")
        self.assertEqual(payload["explanation_filename"], "")
        self.assertEqual(payload["explanation_download_url"], "")
        self.assertTrue(
            ExamAsset.objects.filter(
                exam=exam,
                asset_type=(
                    ExamAsset.AssetType.TEACHER_EXPLANATION_SOURCE
                ),
            ).exists()
        )

    @patch(
        "apps.domains.exams.views.pdf_question_extract_view."
        "upload_fileobj_to_r2_storage"
    )
    def test_executable_source_is_rejected(self, upload_file):
        exam = Exam.objects.create(
            tenant=self.tenant,
            title="위험 파일 거부 시험",
            exam_type=Exam.ExamType.REGULAR,
        )
        upload = SimpleUploadedFile(
            "문제지.exe",
            b"MZ fixture",
            content_type="application/x-msdownload",
        )

        response = PdfQuestionExtractView.as_view()(
            self._request(exam, upload)
        )

        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn("실행 파일", response.data["detail"])
        upload_file.assert_not_called()

    @patch(
        "apps.domains.exams.views.pdf_question_extract_view."
        "upload_fileobj_to_r2_storage"
    )
    def test_long_executable_filename_keeps_its_blocked_suffix(
        self,
        upload_file,
    ):
        exam = Exam.objects.create(
            tenant=self.tenant,
            title="긴 위험 파일명 거부 시험",
            exam_type=Exam.ExamType.REGULAR,
        )
        upload = SimpleUploadedFile(
            f"{'문제자료' * 80}.exe",
            b"MZ fixture",
            content_type="application/octet-stream",
        )

        response = PdfQuestionExtractView.as_view()(
            self._request(exam, upload)
        )

        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn("실행 파일", response.data["detail"])
        upload_file.assert_not_called()

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
    def test_callback_creates_review_proposals_without_canonical_structure(
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
                "explanations": [
                    {
                        "question_number": 2,
                        "text": "선생님이 작성한 풀이",
                        "image_key": f"tenants/{self.tenant.id}/exams/explanations/{exam.id}/2.png",
                        "match_confidence": 1.0,
                        "source_render_mode": "source_content_reconstruction",
                        "source_attachment_image_key": (
                            f"tenants/{self.tenant.id}/exams/explanations-review/"
                            f"{exam.id}/q002-source-attachment.png"
                        ),
                        "source_attachment_requires_review": True,
                    }
                ],
                "answers": [
                    {
                        "question_number": 1,
                        "answer": "4",
                        "source_image_key": (
                            f"tenants/{self.tenant.id}/exams/answer-sources/"
                            f"{exam.id}/page-001.png"
                        ),
                    }
                ],
                "answer_source_requested": True,
                "explanation_source_requested": True,
                "missing_answer_numbers": [2],
                "missing_explanation_numbers": [1],
                "source_issues": [
                    "answer_coverage_incomplete",
                    "explanation_coverage_incomplete",
                ],
                "paired_source_status": "partial",
                "segmentation_method": "hwp_endnote",
            },
            error=None,
            source_id=str(exam.id),
        )

        exam.refresh_from_db()
        self.assertEqual(
            exam.segmentation_status,
            Exam.SegmentationStatus.REVIEW_REQUIRED,
        )
        self.assertFalse(Sheet.objects.filter(exam=exam).exists())
        proposals = list(ExamQuestionProposal.objects.filter(exam=exam))
        self.assertEqual(
            [proposal.number for proposal in proposals],
            [1, 2],
        )
        self.assertEqual(
            proposals[1].explanation_text,
            "선생님이 작성한 풀이",
        )
        self.assertEqual(
            proposals[1].explanation_image_key,
            f"tenants/{self.tenant.id}/exams/explanations/{exam.id}/2.png",
        )
        self.assertEqual(
            proposals[1].region_meta["source_render_mode"],
            "source_content_reconstruction",
        )
        self.assertTrue(
            proposals[1].region_meta["source_attachment_image_key"].endswith(
                "q002-source-attachment.png"
            )
        )
        self.assertEqual(proposals[0].region_meta["answer_candidate"], "4")
        self.assertTrue(proposals[1].region_meta["answer_missing"])
        self.assertTrue(proposals[0].region_meta["explanation_missing"])
        self.assertEqual(
            proposals[0].region_meta["paired_source_status"],
            "partial",
        )
        self.assertFalse(AnswerKey.objects.filter(exam=exam).exists())
        dispatch_matchup.assert_not_called()

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
