from __future__ import annotations

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.models import Tenant, TenantMembership
from apps.domains.exams.models import Exam, ExamAsset
from apps.domains.exams.views.exam_asset_view import ExamAssetView


User = get_user_model()


class ExamAssetUploadTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.tenant = Tenant.objects.create(
            name="Exam Asset Upload",
            code="exam-asset-upload",
            is_active=True,
        )
        self.admin = User.objects.create_user(
            username="exam-asset-upload-admin",
            password="test1234",
            tenant=self.tenant,
            is_staff=True,
        )
        TenantMembership.ensure_active(
            tenant=self.tenant,
            user=self.admin,
            role="admin",
        )
        self.exam = Exam.objects.create(
            tenant=self.tenant,
            title="Upload Template",
            exam_type=Exam.ExamType.TEMPLATE,
        )

    def _post(self, upload: SimpleUploadedFile, asset_type: str):
        request = self.factory.post(
            f"/api/v1/exams/{self.exam.id}/assets/",
            {"asset_type": asset_type, "file": upload},
            format="multipart",
        )
        request.tenant = self.tenant
        force_authenticate(request, user=self.admin)
        return ExamAssetView.as_view()(request, exam_id=self.exam.id)

    @patch("apps.domains.exams.views.exam_asset_view.upload_fileobj_to_r2")
    def test_safe_non_pdf_originals_are_preserved(self, upload_fileobj_to_r2):
        cases = (
            (
                ExamAsset.AssetType.PROBLEM_PDF,
                "problem.docx",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            ),
            (
                ExamAsset.AssetType.OMR_SHEET,
                "answer-sheet.pptx",
                "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            ),
        )

        for asset_type, filename, content_type in cases:
            with self.subTest(asset_type=asset_type):
                response = self._post(
                    SimpleUploadedFile(
                        filename,
                        b"safe office source",
                        content_type=content_type,
                    ),
                    asset_type,
                )

                self.assertEqual(response.status_code, 201, response.data)
                asset = ExamAsset.objects.get(exam=self.exam, asset_type=asset_type)
                self.assertTrue(asset.file_key.endswith(f".{filename.rsplit('.', 1)[-1]}"))
                self.assertEqual(asset.file_type, "application/octet-stream")

        self.assertEqual(upload_fileobj_to_r2.call_count, 2)
        for call in upload_fileobj_to_r2.call_args_list:
            self.assertEqual(call.kwargs["content_type"], "application/octet-stream")

    @patch("apps.domains.exams.views.exam_asset_view.upload_fileobj_to_r2")
    def test_executable_asset_is_rejected_before_storage(self, upload_fileobj_to_r2):
        response = self._post(
            SimpleUploadedFile(
                f"{'a' * 240}.exe",
                b"MZ",
                content_type="application/octet-stream",
            ),
            ExamAsset.AssetType.OMR_SHEET,
        )

        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn("실행 파일", response.data["detail"])
        upload_fileobj_to_r2.assert_not_called()
        self.assertFalse(ExamAsset.objects.filter(exam=self.exam).exists())
