"""Discard/re-upload uses a new receipt without rewriting archived evidence."""
from unittest.mock import patch

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.models import Tenant, TenantMembership
from apps.domains.submissions.models import OmrUploadBatchItem, Submission, SubmissionAnswer
from apps.domains.submissions.views.exam_omr_batch_upload_view import (
    ExamOMRBatchInitializeView, ExamOMRBatchUploadView, OmrUploadBatchDetailView,
    OmrUploadBatchRetryView, _mark_admission_failed, _mark_duplicate_admission,
)
from apps.domains.submissions.views.submission_view import SubmissionViewSet
from apps.domains.submissions.views import exam_omr_batch_upload_view as batch_view

Exam = apps.get_model("exams", "Exam")
Sheet = apps.get_model("exams", "Sheet")
Enrollment = apps.get_model("enrollment", "Enrollment")
Lecture = apps.get_model("lectures", "Lecture")
ResultFact = apps.get_model("results", "ResultFact")
Student = apps.get_model("students", "Student")


class OmrReadmissionFixture:
    def setUp(self):
        super().setUp()
        self.tenant = Tenant.objects.create(code="readmission", name="Readmission")
        self.user = get_user_model().objects.create_user(
            username="readmission-admin", tenant=self.tenant, is_staff=True,
        )
        TenantMembership.ensure_active(tenant=self.tenant, user=self.user, role="admin")
        self.exam = Exam.objects.create(
            tenant=self.tenant, title="Readmission", exam_type=Exam.ExamType.TEMPLATE,
        )
        Sheet.objects.create(exam=self.exam, name="MAIN", total_questions=1)
        self.storage = self.enterContext(patch(
            "apps.domains.submissions.serializers.submission.upload_fileobj_to_r2",
        ))
        self.dispatch = self.enterContext(patch(
            "apps.domains.submissions.views.exam_omr_batch_upload_view.dispatch_submission",
        ))
        self.cleanup = self.enterContext(patch(
            "apps.domains.submissions.views.exam_omr_batch_upload_view.delete_object_r2_ai",
        ))

    def request(self, view, data=None, *, method="post", **kwargs):
        factory = APIRequestFactory()
        request = getattr(factory, method)("/", data=data or {}, format="json")
        request.tenant = self.tenant
        force_authenticate(request, user=self.user)
        return view(request, **kwargs)

    def initialize(self):
        result = self.request(ExamOMRBatchInitializeView.as_view(), {"total_count": 1}, exam_id=self.exam.id)
        self.assertEqual(result.status_code, 201, result.data)
        return result.data["id"]

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


class OmrReadmissionTests(OmrReadmissionFixture, TestCase):
    def test_discard_reupload_preserves_original_and_new_active_dedupe(self):
        old, _ = self.upload()
        original = old.submission
        answer = SubmissionAnswer.objects.create(
            tenant=self.tenant, submission=original, exam_question_id=1, answer="2",
            meta={"source": "teacher", "approved": True},
        )
        lecture = Lecture.objects.create(tenant=self.tenant, title="Teacher history", name="Teacher history")
        student_user = get_user_model().objects.create_user(username="readmission-student", tenant=self.tenant)
        student = Student.objects.create(
            tenant=self.tenant, user=student_user, name="Synthetic student", ps_number="readmission-student",
        )
        enrollment = Enrollment.objects.create(tenant=self.tenant, lecture=lecture, student=student)
        fact = ResultFact.objects.create(
            target_type="exam", target_id=self.exam.id, enrollment=enrollment,
            submission_id=original.id, question_id=1, answer="2", score=7, max_score=10,
            source="manual", meta={"approved_by_teacher": True},
        )
        self.discard(original)
        original_snapshot = Submission.objects.filter(pk=original.pk).values().get()
        answer_snapshot = SubmissionAnswer.objects.filter(pk=answer.pk).values().get()
        fact_snapshot = ResultFact.objects.filter(pk=fact.pk).values().get()
        original_hash = old.content_sha256

        new, response = self.upload()
        self.assertEqual(response.data["created_count"], 1, response.data)
        self.assertNotEqual(new.submission_id, original.id)
        old.refresh_from_db()
        self.assertEqual(old.submission_id, original.id)
        self.assertEqual(old.content_sha256, original_hash)
        self.assertEqual(old.failure_code, "submission_archived")
        self.assertEqual(Submission.objects.filter(pk=original.pk).values().get(), original_snapshot)
        self.assertEqual(SubmissionAnswer.objects.filter(pk=answer.pk).values().get(), answer_snapshot)
        self.assertEqual(ResultFact.objects.filter(pk=fact.pk).values().get(), fact_snapshot)
        duplicate, repeated = self.upload()
        self.assertEqual(repeated.data["created_count"], 0)
        self.assertEqual(repeated.data["counts"]["duplicate"], 1)
        self.assertEqual(duplicate.duplicate_of_submission_id, new.submission_id)
        self.assertEqual(self.storage.call_count, 2)
        self.assertEqual(self.dispatch.call_count, 2)

    def test_archived_ordinal_cannot_be_rebound_or_revived(self):
        old, _ = self.upload()
        self.discard(old.submission)
        new, _ = self.upload()
        old.refresh_from_db()
        snapshot = OmrUploadBatchItem.objects.filter(pk=old.pk).values().get()
        for content in (b"replacement-bytes", b""):
            _, response = self.upload(old.batch_id, content=content)
            self.assertEqual(response.data["created_count"], 0)
            self.assertEqual(OmrUploadBatchItem.objects.filter(pk=old.pk).values().get(), snapshot)
        self.assertFalse(_mark_admission_failed(old.id, code="late_failure", message="late failure"))
        self.assertFalse(_mark_duplicate_admission(
            item_id=old.id, tenant=self.tenant, exam_id=self.exam.id, content_sha256=new.content_sha256,
        ))
        response = self.request(OmrUploadBatchRetryView.as_view(), {"item_ordinals": [1]}, batch_id=old.batch_id)
        self.assertEqual(response.data["retried_ordinals"], [])
        self.assertEqual(response.data["requires_file_ordinals"], [])
        self.assertEqual(response.data["skipped_ordinals"], [1])
        detail = self.request(OmrUploadBatchDetailView.as_view(), method="get", batch_id=old.batch_id)
        self.assertEqual(detail.data["admission_failed_ordinals"], [])
        self.assertEqual(detail.data["failed_ordinals"], [])
        self.assertEqual(OmrUploadBatchItem.objects.filter(pk=old.pk).values().get(), snapshot)
        self.assertEqual(self.storage.call_count, 2)
        self.assertEqual(self.dispatch.call_count, 2)

    def test_superseded_receipt_allows_new_upload_without_rewriting_submission(self):
        old, _ = self.upload()
        Submission.objects.filter(pk=old.submission_id).update(status=Submission.Status.SUPERSEDED)
        before = Submission.objects.filter(pk=old.submission_id).values().get()
        new, response = self.upload()
        self.assertEqual(response.data["created_count"], 1, response.data)
        self.assertNotEqual(new.submission_id, old.submission_id)
        self.assertEqual(Submission.objects.filter(pk=old.submission_id).values().get(), before)
        detail = self.request(OmrUploadBatchDetailView.as_view(), method="get", batch_id=old.batch_id)
        self.assertEqual(detail.data["counts"]["superseded"], 1)

    def test_duplicate_ordinal_preserves_its_original_reference_after_discard(self):
        original, _ = self.upload()
        duplicate, _ = self.upload()
        snapshot = OmrUploadBatchItem.objects.filter(pk=duplicate.pk).values().get()
        self.discard(original.submission)
        for content in (b"same-readmission-scan", b"", b"different-scan"):
            _, response = self.upload(duplicate.batch_id, content=content)
            self.assertEqual(response.data["created_count"], 0)
        self.assertFalse(_mark_admission_failed(duplicate.id, code="late", message="late failure"))
        self.assertFalse(_mark_duplicate_admission(
            item_id=duplicate.id, tenant=self.tenant, exam_id=self.exam.id, content_sha256=original.content_sha256,
        ))
        retry = self.request(OmrUploadBatchRetryView.as_view(), {"item_ordinals": [1]}, batch_id=duplicate.batch_id)
        self.assertEqual(retry.data["skipped_ordinals"], [1])
        self.assertEqual(retry.data["requires_file_ordinals"], [])
        self.assertEqual(OmrUploadBatchItem.objects.filter(pk=duplicate.pk).values().get(), snapshot)
        new, response = self.upload()
        self.assertEqual(response.data["created_count"], 1)
        self.assertNotEqual(new.submission_id, original.submission_id)
        self.assertEqual(self.storage.call_count, 2)
        self.assertEqual(self.dispatch.call_count, 2)

    def test_worker_failure_remains_duplicate_and_retryable(self):
        old, _ = self.upload()
        Submission.objects.filter(pk=old.submission_id).update(
            status=Submission.Status.FAILED, error_message="temporary worker failure",
        )
        duplicate, response = self.upload()
        self.assertEqual(response.data["created_count"], 0)
        self.assertEqual(duplicate.duplicate_of_submission_id, old.submission_id)
        old.refresh_from_db()
        self.assertEqual(old.admission_status, OmrUploadBatchItem.AdmissionStatus.RECEIVED)
        response = self.request(OmrUploadBatchRetryView.as_view(), {"item_ordinals": [1]}, batch_id=old.batch_id)
        self.assertEqual(response.data["retried_ordinals"], [1])
        self.assertEqual(self.storage.call_count, 1)

    def test_only_explicit_archival_releases_the_content_claim(self):
        cases = [
            (Submission.Status.FAILED, {}, "", False),
            (Submission.Status.FAILED, {"discarded": None}, "", False),
            (Submission.Status.FAILED, {"discarded": "yes"}, "", False),
            (Submission.Status.FAILED, {"discarded": []}, "", False),
            (Submission.Status.FAILED, {"discarded": {}}, "", True),
            (Submission.Status.FAILED, {}, "discarded:scan_quality", True),
            (Submission.Status.SUBMITTED, {"discarded": {}}, "", False),
            (Submission.Status.DONE, {}, "", False),
        ]
        for index, (status, meta, error_message, should_create) in enumerate(cases):
            with self.subTest(status=status, meta=meta, error=error_message):
                content = f"classification-{index}".encode()
                original, _ = self.upload(content=content)
                Submission.objects.filter(pk=original.submission_id).update(
                    status=status, meta=meta, error_message=error_message,
                )
                _, response = self.upload(content=content)
                self.assertEqual(response.data["created_count"], int(should_create), response.data)
                self.assertEqual(response.data["counts"]["duplicate"], int(not should_create))

    def test_other_exam_and_tenant_claims_remain_untouched(self):
        old, _ = self.upload()
        self.discard(old.submission)
        before = OmrUploadBatchItem.objects.filter(pk=old.pk).values().get()
        for other_tenant in (False, True):
            if other_tenant:
                self.tenant = Tenant.objects.create(code="readmission-other", name="Other tenant")
                self.user = get_user_model().objects.create_user(
                    username="readmission-other-admin", tenant=self.tenant, is_staff=True,
                )
                TenantMembership.ensure_active(tenant=self.tenant, user=self.user, role="admin")
            self.exam = Exam.objects.create(
                tenant=self.tenant, title="Other exam", exam_type=Exam.ExamType.TEMPLATE,
            )
            Sheet.objects.create(exam=self.exam, name="MAIN", total_questions=1)
            new, response = self.upload()
            self.assertEqual(response.data["created_count"], 1)
            self.assertEqual(new.submission.tenant_id, self.tenant.id)
            self.assertEqual(new.submission.target_id, self.exam.id)
            self.assertEqual(OmrUploadBatchItem.objects.filter(pk=old.pk).values().get(), before)

    def test_integrity_fallback_compensates_only_the_losing_upload(self):
        existing, _ = self.upload()
        owner_lookup = batch_view._received_content_owner
        calls = 0

        def miss_uncommitted_winner(**kwargs):
            nonlocal calls
            calls += 1
            return None if calls == 1 else owner_lookup(**kwargs)

        with patch.object(batch_view, "_received_content_owner", side_effect=miss_uncommitted_winner):
            loser, response = self.upload()
        self.assertEqual(calls, 2)
        self.assertEqual(response.data["created_count"], 0)
        self.assertEqual(response.data["counts"]["duplicate"], 1)
        self.assertEqual(loser.duplicate_of_submission_id, existing.submission_id)
        self.assertEqual(Submission.objects.filter(tenant=self.tenant).count(), 1)
        self.assertEqual(self.dispatch.call_count, 1)
        self.assertEqual(self.cleanup.call_count, 1)
        self.assertNotEqual(self.cleanup.call_args.kwargs["key"], existing.submission.file_key)

    def test_inconsistent_claim_never_links_another_exam_submission(self):
        original, _ = self.upload()
        other_exam = Exam.objects.create(
            tenant=self.tenant, title="Unrelated exam", exam_type=Exam.ExamType.TEMPLATE,
        )
        Submission.objects.filter(pk=original.submission_id).update(target_id=other_exam.id)
        before = OmrUploadBatchItem.objects.filter(pk=original.pk).values().get()
        rejected, response = self.upload()
        self.assertEqual(response.data["created_count"], 0)
        self.assertEqual(response.data["counts"]["duplicate"], 0)
        self.assertIsNone(rejected.submission_id)
        self.assertIsNone(rejected.duplicate_of_submission_id)
        self.assertEqual(OmrUploadBatchItem.objects.filter(pk=original.pk).values().get(), before)
        self.assertEqual(Submission.objects.filter(tenant=self.tenant).count(), 1)

    def test_integrity_fallback_never_reuses_a_retired_winner_and_allows_retry(self):
        original, _ = self.upload()
        self.discard(original.submission)
        original_hash = original.content_sha256
        owner_lookup = batch_view._received_content_owner
        calls = 0

        def miss_stale_claim(**kwargs):
            nonlocal calls
            calls += 1
            return None if calls == 1 else owner_lookup(**kwargs)

        with patch.object(batch_view, "_received_content_owner", side_effect=miss_stale_claim):
            pending, response = self.upload()
        self.assertEqual(response.data["created_count"], 0)
        self.assertEqual(response.data["counts"]["duplicate"], 0)
        self.assertEqual(pending.failure_code, "admission_integrity_failed")
        self.assertIsNone(pending.submission_id)
        self.assertIsNone(pending.duplicate_of_submission_id)
        admitted, recovered = self.upload(pending.batch_id)
        self.assertEqual(recovered.data["created_count"], 1)
        self.assertNotEqual(admitted.submission_id, original.submission_id)
        original.refresh_from_db()
        self.assertEqual(original.content_sha256, original_hash)
        self.assertEqual(original.failure_code, "submission_archived")
        self.assertEqual(self.cleanup.call_count, 1)
