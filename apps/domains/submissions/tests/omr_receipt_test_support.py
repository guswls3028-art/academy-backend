from unittest.mock import patch

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.models import Tenant, TenantMembership
from apps.domains.submissions.models import OmrUploadBatchItem
from apps.domains.submissions.views.exam_omr_batch_upload_view import ExamOMRBatchInitializeView, ExamOMRBatchUploadView
from apps.domains.submissions.views.submission_view import SubmissionViewSet

Exam = apps.get_model("exams", "Exam")
Sheet = apps.get_model("exams", "Sheet")


class OmrReceiptFixture:
    def setUp(self):
        super().setUp()
        self.tenant = Tenant.objects.create(code="receipt-guard", name="Receipt guard")
        self.user = get_user_model().objects.create_user(
            username="receipt-guard-admin", tenant=self.tenant, is_staff=True,
        )
        TenantMembership.ensure_active(tenant=self.tenant, user=self.user, role="admin")
        self.exam = Exam.objects.create(tenant=self.tenant, title="Receipt guard", exam_type=Exam.ExamType.TEMPLATE)
        Sheet.objects.create(exam=self.exam, name="MAIN", total_questions=1)
        self.storage = self.enterContext(patch("apps.domains.submissions.serializers.submission.upload_fileobj_to_r2"))
        self.dispatch = self.enterContext(patch("apps.domains.submissions.views.exam_omr_batch_upload_view.dispatch_submission"))
        self.cleanup = self.enterContext(patch("apps.domains.submissions.views.exam_omr_batch_upload_view.delete_object_r2_ai"))

    def request(self, view, data=None, *, method="post", **kwargs):
        request = getattr(APIRequestFactory(), method)("/", data=data or {}, format="json")
        request.tenant = self.tenant
        force_authenticate(request, user=self.user)
        return view(request, **kwargs)

    def initialize(self):
        response = self.request(ExamOMRBatchInitializeView.as_view(), {"total_count": 1}, exam_id=self.exam.id)
        self.assertEqual(response.status_code, 201, response.data)
        return response.data["id"]

    def upload(self, batch_id=None, *, content=b"same-readmission-scan"):
        batch_id = batch_id or self.initialize()
        request = APIRequestFactory().post("/", {
            "batch_id": str(batch_id), "item_ordinals": ["1"],
            "files": [SimpleUploadedFile("scan.jpg", content, content_type="image/jpeg")],
        }, format="multipart")
        request.tenant = self.tenant
        force_authenticate(request, user=self.user)
        response = ExamOMRBatchUploadView.as_view()(request, exam_id=self.exam.id)
        self.assertEqual(response.status_code, 201, response.data)
        return OmrUploadBatchItem.objects.get(batch_id=batch_id, ordinal=1), response

    def discard(self, submission):
        response = self.request(
            SubmissionViewSet.as_view({"post": "discard"}), {"reason": "wrong_upload"}, pk=submission.id,
        )
        self.assertEqual(response.status_code, 200, response.data)
        submission.refresh_from_db()
