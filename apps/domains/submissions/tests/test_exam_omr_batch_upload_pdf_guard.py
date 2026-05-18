from __future__ import annotations

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.models import Tenant, TenantMembership
from apps.domains.exams.models import Exam, Sheet
from apps.domains.submissions.views.exam_omr_batch_upload_view import (
    ExamOMRBatchUploadView,
)


User = get_user_model()


def _pdf_bytes(page_count: int) -> bytes:
    import fitz

    doc = fitz.open()
    try:
        for _ in range(page_count):
            doc.new_page(width=595, height=842)
        return doc.tobytes()
    finally:
        doc.close()


class ExamOMRBatchUploadPdfGuardTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.tenant = Tenant.objects.create(code="omr-upload", name="OMR Upload")
        self.user = User.objects.create_user(
            username="omr-upload-admin",
            password="pass1234!",
            tenant=self.tenant,
            is_staff=True,
        )
        TenantMembership.ensure_active(tenant=self.tenant, user=self.user, role="admin")
        self.exam = Exam.objects.create(
            tenant=self.tenant,
            title="OMR Exam",
            exam_type=Exam.ExamType.TEMPLATE,
        )
        Sheet.objects.create(exam=self.exam, name="MAIN", total_questions=30)

    def _post(self, upload_file):
        request = self.factory.post(
            f"/api/v1/submissions/submissions/exams/{self.exam.id}/omr/batch/",
            data={"files": [upload_file]},
            format="multipart",
        )
        force_authenticate(request, user=self.user)
        request.tenant = self.tenant
        view = ExamOMRBatchUploadView.as_view()
        return view(request, exam_id=self.exam.id)

    def test_rejects_multipage_pdf_before_creating_submission(self):
        upload = SimpleUploadedFile(
            "scan-bundle.pdf",
            _pdf_bytes(2),
            content_type="application/pdf",
        )

        response = self._post(upload)

        self.assertEqual(response.status_code, 400)
        self.assertIn("2페이지 PDF", response.data["detail"])
        self.assertIn("답안지 1장당 1개 파일", response.data["detail"])

    @patch("apps.domains.submissions.views.exam_omr_batch_upload_view.dispatch_submission")
    @patch("apps.domains.submissions.serializers.submission.upload_fileobj_to_r2")
    def test_accepts_single_page_pdf(self, upload_fileobj_to_r2, dispatch_submission):
        upload = SimpleUploadedFile(
            "one-sheet.pdf",
            _pdf_bytes(1),
            content_type="application/pdf",
        )

        response = self._post(upload)

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["created_count"], 1)
        upload_fileobj_to_r2.assert_called_once()
        dispatch_submission.assert_called_once()
