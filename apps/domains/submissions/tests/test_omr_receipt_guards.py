"""Phase A protects receipt history, including the future phase-B archive shape."""
from unittest.mock import patch

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
        active, active_response = self.upload()
        self.assertEqual(active_response.data["created_count"], 1)
        self.assertNotEqual(active.submission_id, item.submission_id)
        self.assertEqual(active.content_sha256, item.content_sha256)
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
        self.assertEqual(self.storage.call_count, 2)
        self.assertEqual(self.dispatch.call_count, 2)

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

    def test_readmission_releases_archived_hash_claim_but_preserves_original_receipt(self):
        item, _ = self.upload()
        self.discard(item.submission)
        before = OmrUploadBatchItem.objects.filter(pk=item.pk).values().get()
        replacement, response = self.upload()
        self.assertEqual(response.data["created_count"], 1)
        self.assertNotEqual(replacement.submission_id, item.submission_id)
        after = OmrUploadBatchItem.objects.filter(pk=item.pk).values().get()
        self.assertEqual(after["admission_status"], OmrUploadBatchItem.AdmissionStatus.FAILED)
        self.assertEqual(after["failure_code"], "submission_archived")
        mutable = {"admission_status", "failure_code", "failure_message", "updated_at"}
        self.assertEqual({key: value for key, value in after.items() if key not in mutable},
                         {key: value for key, value in before.items() if key not in mutable})
        duplicate, response = self.upload()
        self.assertEqual(response.data["created_count"], 0)
        self.assertEqual(duplicate.duplicate_of_submission_id, replacement.submission_id)
        self.assertEqual(self.storage.call_count, 2)
        self.assertEqual(self.dispatch.call_count, 2)

    def test_ordinary_worker_failure_remains_retryable(self):
        item, _ = self.upload()
        Submission.objects.filter(pk=item.submission_id).update(status=Submission.Status.FAILED, error_message="temporary")
        retry = self.request(OmrUploadBatchRetryView.as_view(), {"item_ordinals": [1]}, batch_id=item.batch_id)
        self.assertEqual(retry.data["retried_ordinals"], [1])
        self.assertEqual(self.dispatch.call_count, 2)

    def test_retry_reports_immediate_dispatch_rejection_for_single_and_batch(self):
        from apps.domains.submissions.services.dispatcher import dispatch_submission
        from apps.domains.submissions.views.submission_view import SubmissionViewSet

        item, _ = self.upload()
        Submission.objects.filter(pk=item.submission_id).update(status=Submission.Status.FAILED)
        self.dispatch.side_effect = dispatch_submission
        with (
            patch("apps.domains.submissions.services.dispatcher._build_ai_payload", return_value={}),
            patch("apps.domains.submissions.services.dispatcher.dispatch_job",
                  return_value={"ok": False, "error": "synthetic-rejection"}),
        ):
            single = self.request(SubmissionViewSet.as_view({"post": "retry"}), pk=item.submission_id)
            batch = self.request(OmrUploadBatchRetryView.as_view(), {"item_ordinals": [1]}, batch_id=item.batch_id)
        self.assertEqual((single.status_code, batch.status_code), (409, 409))
        self.assertEqual(single.data["status"], Submission.Status.FAILED)
        self.assertEqual(batch.data["retried_ordinals"], [])
        self.assertEqual(batch.data["failed_ordinals"], [1])
        self.assertNotIn("synthetic-rejection", single.data["detail"])
        item.submission.refresh_from_db()
        self.assertEqual(item.submission.status, Submission.Status.FAILED)

    def test_batch_retry_counts_only_items_that_started(self):
        from apps.domains.submissions.services.dispatcher import dispatch_submission

        batch_id = self.initialize(total_count=2)
        first, _ = self.upload(batch_id)
        second, _ = self.upload(batch_id, ordinal=2, content=b"another scan")
        Submission.objects.filter(pk__in=[first.submission_id, second.submission_id]).update(status=Submission.Status.FAILED)
        self.dispatch.side_effect = dispatch_submission
        with (
            patch("apps.domains.submissions.services.dispatcher._build_ai_payload", return_value={}),
            patch("apps.domains.submissions.services.dispatcher.dispatch_job",
                  side_effect=[{"ok": False, "error": "rejected"}, {"ok": True, "job_id": "test-new-job"}]),
        ):
            batch = self.request(OmrUploadBatchRetryView.as_view(), {"item_ordinals": [1, 2]}, batch_id=batch_id)
        self.assertEqual(batch.status_code, 200)
        self.assertEqual(batch.data["retried_ordinals"], [2])
        self.assertEqual(batch.data["failed_ordinals"], [1])

    def test_batch_retry_rolls_back_only_the_item_whose_dispatch_raises(self):
        batch_id = self.initialize(total_count=2)
        first, _ = self.upload(batch_id)
        second, _ = self.upload(batch_id, ordinal=2, content=b"another scan")
        Submission.objects.filter(pk__in=[first.submission_id, second.submission_id]).update(
            status=Submission.Status.FAILED, error_message="original failure",
        )
        self.dispatch.side_effect = [RuntimeError("temporary dispatch error"), None]

        response = self.request(OmrUploadBatchRetryView.as_view(), {"item_ordinals": [1, 2]}, batch_id=batch_id)

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["retried_ordinals"], [2])
        self.assertEqual(response.data["failed_ordinals"], [1])
        self.assertEqual(response.data["requires_file_ordinals"], [])
        first.submission.refresh_from_db()
        second.submission.refresh_from_db()
        self.assertEqual(first.submission.status, Submission.Status.FAILED)
        self.assertEqual(first.submission.error_message, "original failure")
        self.assertTrue(first.submission.file_key)
        self.assertEqual(second.submission.status, Submission.Status.SUBMITTED)

        self.dispatch.side_effect = None
        retried = self.request(OmrUploadBatchRetryView.as_view(), {"item_ordinals": [1]}, batch_id=batch_id)
        self.assertEqual(retried.data["retried_ordinals"], [1])
        self.assertEqual(retried.data["failed_ordinals"], [])
        self.assertEqual(self.storage.call_count, 2)
