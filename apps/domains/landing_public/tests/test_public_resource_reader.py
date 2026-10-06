import json
from datetime import timedelta
from pathlib import Path
import subprocess
import uuid
from unittest.mock import patch

from django.utils import timezone
from rest_framework.test import force_authenticate

from apps.domains.landing_public.api.views.resource_views import PublicResourceReaderView
from apps.domains.landing_public.models.resource import PublicResourceFile, PublicResourcePost
from apps.domains.landing_public.services.resource_reader import (
    _run_renderer, handle_public_resource_reader_job, prepare_reader, reader_payload, reader_state,
)
from apps.domains.landing_public.tests.test_public_resources import PublicResourceTestBase
from apps.shared.contracts.ai_job import AIJob

SERVICE = "apps.domains.landing_public.services.resource_reader."


class PublicResourceReaderTests(PublicResourceTestBase):
    def document(self, *, status="ready", published=False):
        file = self.file()
        file.filename = "qa-report.hwpx"
        file.extension = "hwpx"
        file.reader_status = status
        file.reader_token = uuid.uuid4()
        file.reader_requested_at = timezone.now()
        file.storage_key = f"landing-public/resources/{file.tenant_id}/{file.pk}"
        file.reader_data = {"mode": "article", "blocks": [{"kind": "paragraph", "text": "매치업 분석 본문"}],
                            "assets": ["pages.pdf"], "pdf": "pages.pdf", "pages": 1}
        file.reader_object_keys = [f"{file.storage_key}/reader/{file.reader_token}/pages.pdf"]
        if published:
            file.post = PublicResourcePost.objects.create(tenant=self.tenant, author=self.one,
                author_display_name="qa-one", title="qa report", category="matchup")
        file.save()
        return file

    def reader(self, file, *, method="get", user=None, tenant=None):
        request = getattr(self.factory, method)("/qa/", format="json")
        request.tenant = tenant or self.tenant
        if user:
            force_authenticate(request, user=user)
        return PublicResourceReaderView.as_view()(request, file_id=file.pk)

    def job(self, file, **changes):
        return AIJob(id=f"resource-reader-{file.reader_token}", type="public_resource_reader",
                     tenant_id=str(file.tenant_id), source_domain="landing_public_resource",
                     source_id=str(file.pk), payload={"token": str(file.reader_token)}, **changes)

    def test_text_article_publishes_without_forcing_a_download(self):
        file = self.file()
        response = self.call("post", "create", self.body(file, file_ids=[]), user=self.one)
        self.assertEqual(response.status_code, 201)
        result = self.call("get", "retrieve", pk=response.data["id"])
        self.assertEqual(result.data["content"], "qa content")
        self.assertEqual(result.data["files"], [])

    def test_processing_and_failed_reports_cannot_be_published_as_attachments_only(self):
        for state in ("pending", "running", "failed", ""):
            file = self.document(status=state)
            result = self.call("post", "create", self.body(file), user=self.one)
            self.assertEqual(result.status_code, 400)
            file.refresh_from_db()
            self.assertIsNone(file.post_id)
            self.assertTrue(file.is_ready)

    def test_arbitrary_original_requires_an_article_body(self):
        file = self.file(); file.filename = "qa-analysis.zip"; file.save()
        self.assertEqual(self.call("post", "create", self.body(file, content=""), user=self.one).status_code, 400)
        self.assertEqual(self.call("post", "create", self.body(file), user=self.one).status_code, 201)

    def test_reader_preview_is_private_to_active_uploader(self):
        file = self.document()
        with patch(SERVICE + "generate_presigned_get_url_admin", return_value="https://qa.invalid/pages.pdf") as sign:
            for user, tenant in ((None, self.tenant), (self.two, self.tenant), (self.one, self.other)):
                self.assertEqual(self.reader(file, user=user, tenant=tenant).status_code, 404)
            sign.assert_not_called()
            self.assertEqual(self.reader(file, user=self.one).status_code, 200)
            self.one.is_active = False; self.one.save(update_fields=["is_active"])
            self.assertEqual(self.reader(file, user=self.one).status_code, 404)

    @patch(SERVICE + "generate_presigned_get_url_admin", return_value="https://qa.invalid/pages.pdf")
    def test_anonymous_reader_has_fresh_scoped_links_and_deleted_posts_disappear(self, sign):
        file = self.document(published=True)
        response = self.reader(file)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Cache-Control"], "no-store")
        self.assertEqual(response.data["blocks"][0]["text"], "매치업 분석 본문")
        sign.assert_called_once_with(key=file.reader_object_keys[0], expires_in=300)
        self.assertEqual(self.reader(file, tenant=self.other).status_code, 404)
        file.post.status = "deleted"; file.post.save()
        self.assertEqual(self.reader(file).status_code, 404)

    def test_anonymous_reader_cannot_start_jobs(self):
        file = self.document(published=True, status="failed")
        with patch(SERVICE + "create_reader_job") as create:
            self.assertIn(self.reader(file, method="post").status_code, (401, 403))
            create.assert_not_called()

    def test_expired_job_is_retryable_and_enqueue_failure_preserves_original(self):
        file = self.document(status="running")
        token = file.reader_token
        file.reader_requested_at = timezone.now() - timedelta(minutes=11)
        file.save()
        self.assertEqual(reader_state(file), "failed")
        with patch(SERVICE + "publish_reader_job", return_value=False), self.captureOnCommitCallbacks(execute=True):
            prepare_reader(file)
        file.refresh_from_db()
        self.assertNotEqual(file.reader_token, token)
        self.assertEqual(file.reader_status, "failed")
        self.assertTrue(file.is_ready)

    def test_repeated_preparation_does_not_enqueue_duplicate_job(self):
        file = self.document(status="pending")
        with patch(SERVICE + "create_reader_job") as create:
            self.assertEqual(prepare_reader(file).reader_token, file.reader_token)
            create.assert_not_called()

    def test_stale_and_wrong_tenant_worker_never_fetch_original(self):
        file = self.document(status="pending")
        job = self.job(file)
        file.reader_token = uuid.uuid4(); file.save()
        with patch(SERVICE + "get_admin_object_bytes") as fetch:
            handle_public_resource_reader_job(job)
            fetch.assert_not_called()
        wrong = AIJob(id=f"resource-reader-{file.reader_token}", type="public_resource_reader",
                      tenant_id=str(self.other.pk), source_domain="landing_public_resource",
                      source_id=str(file.pk), payload={"token": str(file.reader_token)})
        with patch(SERVICE + "get_admin_object_bytes") as fetch:
            handle_public_resource_reader_job(wrong)
            fetch.assert_not_called()

    def fake_conversion(self, command, **kwargs):
        output = Path(command[-2]); output.mkdir()
        (output / "pages.pdf").write_bytes(b"qa rendered pdf")
        (output / "manifest.json").write_text(json.dumps({"mode": "article",
            "blocks": [{"kind": "paragraph", "text": "끝까지 보존된 본문"}], "assets": ["pages.pdf"], "pdf": "pages.pdf"}))
        self.assertFalse(any(name.startswith("AWS_") or "TOKEN" in name or "SECRET" in name for name in kwargs["env"]))
        self.assertEqual(Path(kwargs["cwd"]), output.parent)

    def test_worker_completes_and_duplicate_delivery_does_not_delete_success(self):
        file = self.document(status="pending")
        with patch(SERVICE + "get_admin_object_bytes", return_value=(b"x" * file.size, "application/hwp+zip")), \
             patch(SERVICE + "_run_renderer", side_effect=self.fake_conversion), \
             patch(SERVICE + "upload_fileobj_to_r2_admin") as upload, \
             patch(SERVICE + "delete_object_r2_admin") as delete:
            handle_public_resource_reader_job(self.job(file))
            file.refresh_from_db()
            self.assertEqual(file.reader_status, "ready")
            self.assertEqual(file.reader_data["blocks"][0]["text"], "끝까지 보존된 본문")
            handle_public_resource_reader_job(self.job(file))
            self.assertEqual(upload.call_count, 1)
            delete.assert_not_called()

    def test_worker_cannot_replace_a_newer_retry_or_delete_its_objects(self):
        file = self.document(status="pending")
        old_key = file.reader_object_keys[0]
        new_token = uuid.uuid4()
        def replace(**kwargs):
            PublicResourceFile.objects.filter(pk=file.pk).update(reader_token=new_token, reader_status="pending")
        with patch(SERVICE + "get_admin_object_bytes", return_value=(b"x" * file.size, "application/hwp+zip")), \
             patch(SERVICE + "_run_renderer", side_effect=self.fake_conversion), \
             patch(SERVICE + "upload_fileobj_to_r2_admin", side_effect=replace), \
             patch(SERVICE + "delete_object_r2_admin") as delete:
            handle_public_resource_reader_job(self.job(file))
            file.refresh_from_db()
            self.assertEqual(file.reader_token, new_token)
            self.assertEqual(file.reader_status, "pending")
            delete.assert_called_once_with(key=old_key)

    def test_attachment_order_survives_save_reload_and_edit(self):
        first, second = self.file(), self.file()
        result = self.call("post", "create", self.body(first, file_ids=[str(second.pk), str(first.pk)]), user=self.one)
        self.assertEqual([item["id"] for item in result.data["files"]], [str(second.pk), str(first.pk)])
        post_id = result.data["id"]
        result = self.call("patch", "partial_update", self.body(first, file_ids=[str(first.pk), str(second.pk)]), user=self.two, pk=post_id)
        self.assertEqual(result.status_code, 200)
        result = self.call("get", "retrieve", pk=post_id)
        self.assertEqual([item["id"] for item in result.data["files"]], [str(first.pk), str(second.pk)])

    def test_timeout_kills_the_entire_native_process_group(self):
        with patch(SERVICE + "subprocess.Popen") as spawn, patch(SERVICE + "os.killpg") as kill:
            process = spawn.return_value.__enter__.return_value
            process.pid = 1234
            process.wait.side_effect = [subprocess.TimeoutExpired("qa", 150), 0]
            with self.assertRaises(subprocess.TimeoutExpired):
                _run_renderer(["qa"], env={}, cwd="/tmp")
            self.assertTrue(spawn.call_args.kwargs["start_new_session"])
            kill.assert_called_once_with(1234, 9)

    def test_reader_asset_traversal_is_rejected_before_signing(self):
        file = self.document()
        file.reader_data["assets"] = ["../../other-tenant/report.pdf"]
        with patch(SERVICE + "generate_presigned_get_url_admin") as sign:
            with self.assertRaises(ValueError):
                reader_payload(file)
            sign.assert_not_called()


    def test_order_changed_after_lost_create_returns_conflict_without_reordering(self):
        first, second = self.file(), self.file()
        request_id = str(uuid.uuid4())
        initial = self.body(first, request_id=request_id, file_ids=[str(first.pk), str(second.pk)])
        result = self.call("post", "create", initial, user=self.one)
        self.assertEqual(result.status_code, 201)
        changed = {**initial, "file_ids": [str(second.pk), str(first.pk)]}
        retry = self.call("post", "create", changed, user=self.one)
        self.assertEqual(retry.status_code, 409)
        reloaded = self.call("get", "retrieve", pk=result.data["id"])
        self.assertEqual([item["id"] for item in reloaded.data["files"]], initial["file_ids"])

    def test_queue_failure_does_not_leave_a_pending_job(self):
        from apps.support.landing_public.resource_reader_jobs import create_reader_job
        created = []
        def create(**kwargs):
            job = create_reader_job(**kwargs)
            created.append(job)
            return job
        file = self.document(status="failed")
        with patch(SERVICE + "create_reader_job", side_effect=create), \
             patch(SERVICE + "publish_reader_job", return_value=False), self.captureOnCommitCallbacks(execute=True):
            prepare_reader(file)
        file.refresh_from_db()
        job = created[0]; job.refresh_from_db()
        self.assertEqual(job.status, "FAILED")
        self.assertIsNotNone(job.completed_at)
        self.assertTrue(file.is_ready)

    def test_prepare_denied_is_not_disguised_as_service_unavailable(self):
        file = self.document(status="failed")
        self.one.is_active = False; self.one.save(update_fields=["is_active"])
        self.assertEqual(self.reader(file, user=self.one, method="post").status_code, 403)
