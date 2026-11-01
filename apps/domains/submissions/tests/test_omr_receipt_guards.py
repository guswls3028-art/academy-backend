"""Phase A protects receipt history, including the future phase-B archive shape."""
from django.test import TestCase

from apps.domains.submissions.models import OmrUploadBatchItem, Submission, SubmissionAnswer
from apps.domains.submissions.tests.omr_receipt_test_support import OmrReceiptFixture
from apps.domains.submissions.views.exam_omr_batch_upload_view import (
    OmrUploadBatchDetailView, OmrUploadBatchRetryView, _mark_admission_failed, _mark_duplicate_admission,
)


class OmrReceiptGuardTests(OmrReceiptFixture, TestCase):
    def archive_shape(self, *, superseded=False):
        item, _ = self.upload()
        if superseded:
            Submission.objects.filter(pk=item.submission_id).update(status=Submission.Status.SUPERSEDED)
        else:
            self.discard(item.submission)
        # This durable representation is produced only by the later activation.
        OmrUploadBatchItem.objects.filter(pk=item.pk).update(
            admission_status=OmrUploadBatchItem.AdmissionStatus.FAILED, failure_code="submission_archived",
        )
        item.refresh_from_db()
        return item

    def test_phase_b_archive_shape_is_immutable_for_valid_and_empty_resends(self):
        item = self.archive_shape()
        answer = SubmissionAnswer.objects.create(
            tenant=self.tenant, submission_id=item.submission_id, exam_question_id=1,
            answer="2", meta={"source": "teacher", "approved": True},
        )
        receipt = OmrUploadBatchItem.objects.filter(pk=item.pk).values().get()
        submission = Submission.objects.filter(pk=item.submission_id).values().get()
        teacher_answer = SubmissionAnswer.objects.filter(pk=answer.pk).values().get()
        for content in (b"replacement", b""):
            _, response = self.upload(item.batch_id, content=content)
            self.assertEqual(response.data["created_count"], 0)
        self.assertFalse(_mark_admission_failed(item.id, code="late", message="late failure"))
        self.assertFalse(_mark_duplicate_admission(
            item_id=item.id, tenant=self.tenant, exam_id=self.exam.id, content_sha256=item.content_sha256,
        ))
        self.assertEqual(OmrUploadBatchItem.objects.filter(pk=item.pk).values().get(), receipt)
        self.assertEqual(Submission.objects.filter(pk=item.submission_id).values().get(), submission)
        self.assertEqual(SubmissionAnswer.objects.filter(pk=answer.pk).values().get(), teacher_answer)
        self.assertEqual(self.storage.call_count, 1)
        self.assertEqual(self.dispatch.call_count, 1)

    def test_duplicate_ordinal_stays_bound_after_original_discard(self):
        original, _ = self.upload()
        duplicate, _ = self.upload()
        self.discard(original.submission)
        before = OmrUploadBatchItem.objects.filter(pk=duplicate.pk).values().get()
        for content in (b"replacement", b"", b"same-readmission-scan"):
            _, response = self.upload(duplicate.batch_id, content=content)
            self.assertEqual(response.data["created_count"], 0)
        self.assertFalse(_mark_admission_failed(duplicate.id, code="late", message="late failure"))
        self.assertFalse(_mark_duplicate_admission(
            item_id=duplicate.id, tenant=self.tenant, exam_id=self.exam.id, content_sha256=original.content_sha256,
        ))
        retry = self.request(OmrUploadBatchRetryView.as_view(), {"item_ordinals": [1]}, batch_id=duplicate.batch_id)
        self.assertEqual(retry.data["skipped_ordinals"], [1])
        self.assertEqual(retry.data["requires_file_ordinals"], [])
        self.assertEqual(OmrUploadBatchItem.objects.filter(pk=duplicate.pk).values().get(), before)

    def test_discarded_receipt_cannot_be_retried_even_before_retirement(self):
        item, _ = self.upload()
        self.discard(item.submission)
        before = Submission.objects.filter(pk=item.submission_id).values().get()
        retry = self.request(OmrUploadBatchRetryView.as_view(), {"item_ordinals": [1]}, batch_id=item.batch_id)
        self.assertEqual(retry.data["retried_ordinals"], [])
        self.assertEqual(retry.data["requires_file_ordinals"], [])
        self.assertEqual(retry.data["skipped_ordinals"], [1])
        self.assertEqual(Submission.objects.filter(pk=item.submission_id).values().get(), before)
        detail = self.request(OmrUploadBatchDetailView.as_view(), method="get", batch_id=item.batch_id)
        self.assertEqual(detail.data["failed_ordinals"], [])
        self.assertEqual(detail.data["admission_failed_ordinals"], [])

    def test_phase_b_archived_and_superseded_receipts_are_not_retry_inputs(self):
        for superseded in (False, True):
            with self.subTest(superseded=superseded):
                item = self.archive_shape(superseded=superseded)
                before = OmrUploadBatchItem.objects.filter(pk=item.pk).values().get()
                retry = self.request(OmrUploadBatchRetryView.as_view(), {"item_ordinals": [1]}, batch_id=item.batch_id)
                self.assertEqual(retry.data["retried_ordinals"], [])
                self.assertEqual(retry.data["requires_file_ordinals"], [])
                self.assertEqual(retry.data["skipped_ordinals"], [1])
                detail = self.request(OmrUploadBatchDetailView.as_view(), method="get", batch_id=item.batch_id)
                self.assertEqual(detail.data["failed_ordinals"], [])
                self.assertEqual(detail.data["admission_failed_ordinals"], [])
                self.assertEqual(detail.data["counts"]["superseded" if superseded else "failed"], 1)
                self.assertEqual(OmrUploadBatchItem.objects.filter(pk=item.pk).values().get(), before)

    def test_phase_a_does_not_release_hash_reservations_or_enable_readmission(self):
        item, _ = self.upload()
        self.discard(item.submission)
        before = OmrUploadBatchItem.objects.filter(pk=item.pk).values().get()
        duplicate, response = self.upload()
        self.assertEqual(response.data["created_count"], 0)
        self.assertEqual(response.data["counts"]["duplicate"], 1)
        self.assertEqual(duplicate.duplicate_of_submission_id, item.submission_id)
        self.assertEqual(OmrUploadBatchItem.objects.filter(pk=item.pk).values().get(), before)
        self.assertEqual(self.storage.call_count, 1)
        self.assertEqual(self.dispatch.call_count, 1)

    def test_ordinary_worker_failure_remains_retryable(self):
        item, _ = self.upload()
        Submission.objects.filter(pk=item.submission_id).update(status=Submission.Status.FAILED, error_message="temporary")
        retry = self.request(OmrUploadBatchRetryView.as_view(), {"item_ordinals": [1]}, batch_id=item.batch_id)
        self.assertEqual(retry.data["retried_ordinals"], [1])
        self.assertEqual(self.dispatch.call_count, 2)
