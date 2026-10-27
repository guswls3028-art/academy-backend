from __future__ import annotations

from datetime import timedelta
import io
import uuid
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone

from apps.core.models import Tenant
from apps.domains.submissions.models import HomeworkMediaPreview, SubmissionMedia, SubmissionStorageCleanupIntent
from apps.domains.submissions.services.homework_preview import JOB_LEASE, prepare_preview
from apps.domains.submissions.tests import test_homework_media_submission as fixtures
from apps.domains.submissions.views.homework_submission_media_view import HomeworkSubmissionCompatiblePreviewView
from apps.worker.video_worker.homework_preview_main import process_preview


@override_settings(VIDEO_BATCH_JOB_QUEUE="academy-development-video-queue", VIDEO_BATCH_JOB_DEFINITION="academy-development-video-job")
class HomeworkPreviewTests(TestCase):
    setUp = fixtures.HomeworkSubmissionMediaTests.setUp
    _request = fixtures.HomeworkSubmissionMediaTests._request
    _post = fixtures.HomeworkSubmissionMediaTests._post
    _link_parent = fixtures.HomeworkSubmissionMediaTests._link_parent

    def video(self):
        with patch("apps.domains.submissions.services.homework_media.upload_fileobj_to_r2"):
            response = self._post(file=fixtures._mp4())
        self.assertEqual(response.status_code, 201)
        return SubmissionMedia.objects.get(pk=int(response.data["id"]))

    def request_preview(self, media, method="post", **kwargs):
        return HomeworkSubmissionCompatiblePreviewView.as_view()(
            self._request(method, "/compatible/", **kwargs), homework_id=self.homework.pk, media_id=str(media.pk),
        )

    def prepare(self, media):
        return prepare_preview(submission=media.submission, media_id=str(media.pk), source_key=media.object_key, media_kind="video")

    @patch("apps.domains.submissions.services.homework_preview.submit_homework_preview_batch_job", return_value="batch-owned")
    def test_read_is_non_mutating_and_repeated_prepare_enqueues_once(self, submit):
        media = self.video()
        self.assertEqual(self.request_preview(media, "get").data, {"status": "not_requested"})
        self.assertEqual(HomeworkMediaPreview.objects.count(), 0)
        self.assertEqual(self.request_preview(media).status_code, 202)
        self.assertEqual(self.request_preview(media).status_code, 202)
        self.assertEqual(submit.call_count, 1)
        preview = HomeworkMediaPreview.objects.get()
        self.assertEqual(preview.source_key, media.object_key)
        self.assertNotEqual(preview.object_key, media.object_key)
        self.assertIn(f"tenants/{self.tenant.pk}/ai/submissions/{media.submission_id}/", preview.object_key)

    @override_settings(VIDEO_BATCH_JOB_QUEUE="", VIDEO_BATCH_JOB_DEFINITION="")
    @patch("apps.domains.submissions.services.homework_preview.submit_homework_preview_batch_job")
    def test_unconfigured_isolated_runtime_never_falls_back_to_production(self, submit):
        response = self.request_preview(self.video())
        self.assertEqual(response.status_code, 503)
        submit.assert_not_called()
        self.assertEqual(HomeworkMediaPreview.objects.count(), 0)

    @patch("apps.domains.submissions.services.homework_preview.submit_homework_preview_batch_job", return_value="batch-owned")
    def test_cross_tenant_and_removed_source_are_not_servable(self, submit):
        media = self.video()
        other = Tenant.objects.create(code="preview-other", name="Other", is_active=True)
        self.assertIn(self.request_preview(media, tenant=other).status_code, (403, 404))
        media.removed_at = timezone.now()
        media.save(update_fields=["removed_at"])
        self.assertEqual(self.request_preview(media).status_code, 404)
        submit.assert_not_called()

    @patch("apps.domains.submissions.services.homework_preview.submit_homework_preview_batch_job", return_value="batch-owned")
    def test_parent_selected_child_and_teacher_share_same_private_derivative(self, submit):
        media = self.video()
        self._link_parent()
        self.assertEqual(self.request_preview(media, user=self.parent_user, student_id=self.student.pk).status_code, 202)
        self.assertEqual(self.request_preview(media, user=self.teacher).status_code, 202)
        self.assertEqual(submit.call_count, 1)

    @patch("apps.domains.submissions.services.homework_preview.submit_homework_preview_batch_job", return_value="batch-owned")
    @patch("apps.domains.submissions.services.homework_preview.generate_presigned_get_url", return_value="https://preview.invalid/owned")
    def test_ready_copy_uses_short_signed_url_and_preserves_original(self, presign, submit):
        media = self.video()
        preview = self.prepare(media)
        preview.status = HomeworkMediaPreview.Status.READY
        preview.save()
        payload = self.request_preview(media, "get").data
        self.assertEqual(payload["status"], "ready")
        presign.assert_called_once_with(key=preview.object_key, expires_in=600)
        media.refresh_from_db()
        self.assertEqual(media.object_key, preview.source_key)
        self.assertEqual(media.status, SubmissionMedia.Status.UPLOADED)

    @patch("apps.domains.submissions.services.homework_preview.submit_homework_preview_batch_job", return_value="batch-owned")
    @patch("apps.domains.submissions.services.homework_preview.schedule_unreferenced_ai_object_cleanup")
    def test_stale_attempt_cannot_publish_after_retry(self, cleanup, submit):
        media = self.video()
        first = self.prepare(media)
        HomeworkMediaPreview.objects.filter(pk=first.pk).update(updated_at=timezone.now() - JOB_LEASE - timedelta(seconds=1))
        self.assertEqual(self.request_preview(media, "get").data["status"], "failed")
        second = self.prepare(media)
        self.assertNotEqual(first.attempt_token, second.attempt_token)
        self.assertNotEqual(first.object_key, second.object_key)
        cleanup.assert_called_once_with(tenant_id=self.tenant.pk, key=first.object_key)
        with patch.dict("os.environ", {"AWS_BATCH_JOB_ID": "qa-job"}):
            self.assertFalse(process_preview(str(first.pk), str(first.attempt_token)))

    @patch("apps.domains.submissions.services.homework_preview.submit_homework_preview_batch_job", side_effect=RuntimeError("private provider detail"))
    def test_enqueue_failure_exposes_no_provider_details_and_allows_retry(self, submit):
        response = self.request_preview(self.video())
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("private", str(response.data))
        self.assertEqual(HomeworkMediaPreview.objects.get().status, HomeworkMediaPreview.Status.FAILED)

    def test_video_worker_rejects_non_batch_execution(self):
        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "AWS Batch"):
                process_preview(str(uuid.uuid4()), str(uuid.uuid4()))

    @patch("apps.domains.submissions.services.homework_preview.submit_homework_preview_batch_job", return_value="batch-owned")
    @patch("apps.infrastructure.storage.r2._get_s3_client")
    @patch("apps.infrastructure.storage.r2.upload_fileobj_to_r2")
    @patch("apps.worker.video_worker.homework_preview_main.convert_video")
    def test_worker_reads_only_owned_source_and_publishes_current_attempt(self, convert, upload, s3, submit):
        media = self.video()
        preview = self.prepare(media)
        body = io.BytesIO(b"synthetic-video")
        s3.return_value.get_object.return_value = {"Body": body, "ContentLength": 15}
        convert.side_effect = lambda source, output: output.write_bytes(b"synthetic-compatible-mp4")
        with patch.dict("os.environ", {"AWS_BATCH_JOB_ID": "qa-job"}):
            self.assertTrue(process_preview(str(preview.pk), str(preview.attempt_token)))
        preview.refresh_from_db()
        self.assertEqual(preview.status, HomeworkMediaPreview.Status.READY)
        self.assertEqual(s3.return_value.get_object.call_args.kwargs["Key"], media.object_key)
        self.assertEqual(upload.call_args.kwargs["key"], preview.object_key)
        self.assertTrue(body.closed)

    @patch("apps.domains.submissions.services.homework_preview.submit_homework_preview_batch_job", return_value="batch-owned")
    @patch("apps.infrastructure.storage.r2._get_s3_client")
    @patch("apps.infrastructure.storage.r2.upload_fileobj_to_r2", side_effect=OSError("uncertain upload"))
    @patch("apps.worker.video_worker.homework_preview_main.convert_video")
    @patch("apps.domains.submissions.services.lifecycle.schedule_unreferenced_ai_object_cleanup")
    def test_uncertain_upload_detaches_and_schedules_exact_derived_key_cleanup(self, cleanup, convert, upload, s3, submit):
        preview = self.prepare(self.video())
        reserved = preview.object_key
        s3.return_value.get_object.return_value = {"Body": io.BytesIO(b"video"), "ContentLength": 5}
        convert.side_effect = lambda source, output: output.write_bytes(b"compatible")
        with patch.dict("os.environ", {"AWS_BATCH_JOB_ID": "qa-job"}):
            self.assertFalse(process_preview(str(preview.pk), str(preview.attempt_token)))
        preview.refresh_from_db()
        self.assertEqual(preview.status, HomeworkMediaPreview.Status.FAILED)
        self.assertEqual(preview.object_key, "")
        cleanup.assert_called_once_with(tenant_id=self.tenant.pk, key=reserved)

    @patch("apps.domains.submissions.services.homework_preview.submit_homework_preview_batch_job", return_value="batch-owned")
    def test_development_uses_only_pinned_homework_queue_and_preprod_never_enqueues(self, submit):
        media = self.video()
        with patch.dict("os.environ", {"ACADEMY_RUNTIME_ENV": "development"}):
            self.assertEqual(self.request_preview(media).status_code, 503)
            with override_settings(HOMEWORK_PREVIEW_BATCH_JOB_QUEUE="academy-development-homework-video-queue",
                    HOMEWORK_PREVIEW_BATCH_JOB_DEFINITION="academy-development-homework-video-abcdef123456-v17:1"):
                self.assertEqual(self.request_preview(media).status_code, 202)
                self.assertEqual(submit.call_args.kwargs["queue_name"], "academy-development-homework-video-queue")
        with patch.dict("os.environ", {"ACADEMY_RUNTIME_ENV": "preprod"}):
            self.assertEqual(self.request_preview(media).status_code, 503)
        self.assertEqual(submit.call_count, 1)

    @patch("apps.domains.submissions.services.homework_preview.submit_homework_preview_batch_job", return_value="batch-owned")
    def test_permanent_delete_records_original_and_derived_keys_before_removing_rows(self, submit):
        from apps.domains.submissions.services.lifecycle import delete_submission_storage_for_permanent_delete
        media = self.video()
        preview = self.prepare(media)
        delete_submission_storage_for_permanent_delete(tenant_id=self.tenant.pk, submission_ids=[media.submission_id])
        self.assertFalse(HomeworkMediaPreview.objects.filter(pk=preview.pk).exists())
        self.assertFalse(SubmissionMedia.objects.filter(pk=media.pk).exists())
        keys = set(SubmissionStorageCleanupIntent.objects.filter(tenant=self.tenant).values_list("object_key", flat=True))
        self.assertEqual(keys, {media.object_key, preview.object_key})

    @patch("apps.domains.submissions.services.homework_preview.submit_homework_preview_batch_job", return_value="batch-owned")
    @patch("apps.infrastructure.storage.r2._get_s3_client")
    @patch("apps.infrastructure.storage.r2.upload_fileobj_to_r2")
    @patch("apps.worker.video_worker.homework_preview_main.convert_video")
    @patch("apps.domains.submissions.services.lifecycle.schedule_unreferenced_ai_object_cleanup")
    def test_removed_source_cannot_publish_a_late_conversion(self, cleanup, convert, upload, s3, submit):
        media = self.video()
        preview = self.prepare(media)
        reserved = preview.object_key
        s3.return_value.get_object.return_value = {"Body": io.BytesIO(b"video"), "ContentLength": 5}
        def finish_after_removal(source, output):
            SubmissionMedia.objects.filter(pk=media.pk).update(removed_at=timezone.now())
            output.write_bytes(b"compatible")
        convert.side_effect = finish_after_removal
        with patch.dict("os.environ", {"AWS_BATCH_JOB_ID": "qa-job"}):
            self.assertFalse(process_preview(str(preview.pk), str(preview.attempt_token)))
        preview.refresh_from_db()
        self.assertEqual(preview.status, HomeworkMediaPreview.Status.FAILED)
        self.assertEqual(preview.object_key, "")
        cleanup.assert_called_once_with(tenant_id=self.tenant.pk, key=reserved)
