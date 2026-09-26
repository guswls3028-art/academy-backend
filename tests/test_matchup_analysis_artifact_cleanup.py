"""Analysis jobs retire only their own detached R2 images."""

import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
from PIL import Image
from django.test import TestCase

from apps.core.models import Tenant
from apps.domains.ai.callbacks import _handle_matchup_ai_result
from apps.domains.ai.models import AIJobModel
from apps.domains.inventory.models import InventoryFile
from apps.domains.matchup.analysis_artifacts import analysis_artifact_prefix, process_artifact_scan_intents
from apps.domains.matchup.models import MatchupArtifactScanIntent, MatchupDocument, MatchupProblem
from apps.domains.matchup.services import retry_document
from apps.domains.submissions.models import SubmissionStorageCleanupIntent


class AnalysisArtifactCleanupTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(code="qa-analysis-cleanup", name="QA cleanup")
        self.other_tenant = Tenant.objects.create(code="qa-analysis-other", name="QA other")
        original_key = f"tenants/{self.tenant.id}/admin/inventory/source.pdf"
        inventory = InventoryFile.objects.create(
            tenant=self.tenant, scope="admin", display_name="source.pdf",
            original_name="source.pdf", r2_key=original_key, size_bytes=0,
        )
        self.doc = MatchupDocument.objects.create(
            tenant=self.tenant, inventory_file=inventory, title="source",
            r2_key=original_key, original_name="source.pdf", status="processing",
        )

    def _problem(self, number, key, meta):
        return MatchupProblem.objects.create(
            tenant=self.tenant, document=self.doc, number=number,
            image_key=key, meta=meta,
        )

    def _intent_keys(self):
        return set(SubmissionStorageCleanupIntent.objects.filter(
            tenant=self.tenant,
        ).values_list("object_key", flat=True))

    def test_worker_uploads_crops_and_pages_under_same_job_scope(self):
        from academy.application.use_cases.ai.pipelines.matchup_pipeline import (
            _cleanup_cropped_image_temps,
            _upload_cropped_images,
            _upload_page_images_for_modal_cache,
        )

        job_id = "worker-job-scope"
        prefix = analysis_artifact_prefix(tenant_id=self.tenant.id, job_id=job_id)
        with tempfile.TemporaryDirectory() as folder:
            source = str(Path(folder) / "page.png")
            Image.new("RGB", (16, 16), "white").save(source)
            questions = [{"number": 1, "image_path": source, "bbox": [0, 0, 12, 12]}]
            pages = [{"page_index": 0, "image_path": source}]
            try:
                with patch("apps.infrastructure.storage.r2.upload_fileobj_to_r2_storage") as upload:
                    _upload_cropped_images(questions, str(self.tenant.id), str(self.doc.id), job_id)
                    page_keys, _ = _upload_page_images_for_modal_cache(
                        pages, str(self.tenant.id), str(self.doc.id), job_id,
                    )
            finally:
                _cleanup_cropped_image_temps(questions)

        self.assertEqual(upload.call_count, 2)
        self.assertTrue(questions[0]["image_key"].startswith(prefix + "problems/"))
        self.assertEqual(page_keys, [prefix + "pages/000.png"])

    def test_retry_detaches_only_automatic_images_and_preserves_user_work(self):
        prefix = f"tenants/{self.tenant.id}/matchup/legacy/"
        generated_public_key = prefix + "public-cleanup/1.png"
        automatic = self._problem(1, prefix + "problems/1.png", {
            "public_cleanup": {"status": "ready", "public_image_key": generated_public_key},
        })
        manual = self._problem(2, prefix + "problems/2.png", {"manual": True})
        pinned = self._problem(3, prefix + "problems/3.png", {"manual_owner_pinned": True})
        approved = self._problem(4, prefix + "problems/4.png", {"confirmation_status": "confirmed"})
        foreign_key = f"tenants/{self.other_tenant.id}/matchup/legacy/problems/5.png"
        self._problem(5, foreign_key, {})
        public_approved = self._problem(6, prefix + "problems/6.png", {
            "public_cleanup": {"status": "approved", "public_image_key": prefix + "public-cleanup/6.png"},
        })
        self.doc.status = "failed"
        self.doc.save(update_fields=["status"])

        with patch("apps.infrastructure.storage.r2.generate_presigned_get_url_storage", return_value="https://example.test/source"), patch(
            "apps.domains.matchup.services.dispatch_ai_job",
            return_value={"ok": True, "job_id": "new-job"},
        ):
            retry_document(self.doc, require_failed=True)

        self.assertFalse(MatchupProblem.objects.filter(id=automatic.id).exists())
        self.assertEqual(
            set(self.doc.problems.values_list("id", flat=True)),
            {manual.id, pinned.id, approved.id, public_approved.id},
        )
        self.assertEqual(self._intent_keys(), {automatic.image_key, generated_public_key})
        self.assertNotIn(foreign_key, self._intent_keys())

    def test_done_callback_cleans_rejected_and_unreferenced_job_keys(self):
        job_id = "job-with-crop-conflict"
        self.doc.ai_job_id = job_id
        self.doc.save(update_fields=["ai_job_id"])
        AIJobModel.objects.create(
            job_id=job_id, job_type="matchup_analysis", tenant_id=str(self.tenant.id),
            source_domain="matchup", source_id=str(self.doc.id),
        )
        prefix = analysis_artifact_prefix(tenant_id=self.tenant.id, job_id=job_id)
        accepted = prefix + "problems/run/1.png"
        rejected = prefix + "problems/run/2.png"
        unused_page = prefix + "pages/000.png"
        foreign_key = f"tenants/{self.other_tenant.id}/matchup/jobs/unrelated/problems/1.png"
        manual = self._problem(2, "manual-owned-key", {"manual": True})

        with patch(
            "academy.adapters.storage.r2_objects.iter_r2_objects",
            return_value=[{"Key": key} for key in (accepted, rejected, unused_page, foreign_key)],
        ), patch("academy.application.use_cases.ai.segmentation.fingerprint_collector.collect_and_save"):
            _handle_matchup_ai_result(
                job_id=job_id, status="DONE", source_id=str(self.doc.id), error=None,
                result_payload={
                    "segmentation_method": "image",
                    "problems": [
                        {"number": 1, "image_key": accepted, "meta": {"page_index": 0, "bbox": {"x": 0, "y": 0, "w": .4, "h": .4}}},
                        {"number": 2, "image_key": rejected, "meta": {"page_index": 0, "bbox": {"x": .5, "y": 0, "w": .4, "h": .4}}},
                    ],
                },
            )

        self.doc.refresh_from_db()
        self.assertEqual(self.doc.status, "done")
        self.assertTrue(self.doc.problems.filter(id=manual.id, image_key="manual-owned-key").exists())
        self.assertTrue(self.doc.problems.filter(number=1, image_key=accepted).exists())
        self.assertEqual(self._intent_keys(), {rejected, unused_page})

    def test_failed_callback_queues_partial_uploads_from_exact_job(self):
        job_id = "job-partial-failure"
        self.doc.ai_job_id = job_id
        self.doc.save(update_fields=["ai_job_id"])
        AIJobModel.objects.create(
            job_id=job_id, job_type="matchup_analysis", tenant_id=str(self.tenant.id),
            source_domain="matchup", source_id=str(self.doc.id),
        )
        key = analysis_artifact_prefix(tenant_id=self.tenant.id, job_id=job_id) + "problems/run/1.png"
        with patch("academy.adapters.storage.r2_objects.iter_r2_objects", return_value=[{"Key": key}]):
            _handle_matchup_ai_result(
                job_id=job_id, status="FAILED", source_id=str(self.doc.id),
                result_payload={}, error="worker failed after upload",
            )

        self.doc.refresh_from_db()
        self.assertEqual(self.doc.status, "failed")
        self.assertEqual(self._intent_keys(), {key})

    def test_failed_callback_retries_scoped_listing_after_r2_recovers(self):
        from apps.domains.submissions.services.lifecycle import process_submission_storage_cleanup_intents

        job_id = "job-listing-outage"
        self.doc.ai_job_id = job_id
        self.doc.save(update_fields=["ai_job_id"])
        AIJobModel.objects.create(
            job_id=job_id, job_type="matchup_analysis", tenant_id=str(self.tenant.id),
            source_domain="matchup", source_id=str(self.doc.id),
        )
        prefix = analysis_artifact_prefix(tenant_id=self.tenant.id, job_id=job_id)
        key = prefix + "problems/run/1.png"
        foreign = f"tenants/{self.other_tenant.id}/matchup/jobs/foreign/problems/1.png"
        with patch(
            "academy.adapters.storage.r2_objects.iter_r2_objects",
            side_effect=RuntimeError("R2 listing unavailable"),
        ):
            _handle_matchup_ai_result(
                job_id=job_id, status="FAILED", source_id=str(self.doc.id),
                result_payload={}, error="worker failed after upload",
            )

        scan = MatchupArtifactScanIntent.objects.get(
            tenant=self.tenant,
            document_id=self.doc.id,
            job_id=job_id,
        )
        self.doc.refresh_from_db()
        self.assertEqual(self.doc.status, "failed")
        self.assertEqual(scan.status, MatchupArtifactScanIntent.Status.PENDING)
        with patch("apps.infrastructure.storage.r2.delete_object_r2_storage") as delete:
            old_consumer = process_submission_storage_cleanup_intents()
        self.assertEqual(old_consumer.cleaned, 0)
        delete.assert_not_called()
        scan.refresh_from_db()
        self.assertEqual(scan.status, MatchupArtifactScanIntent.Status.PENDING)

        with patch(
            "academy.adapters.storage.r2_objects.iter_r2_objects",
            side_effect=RuntimeError("R2 still unavailable"),
        ):
            failed = process_artifact_scan_intents(intent_ids=[scan.id])
        self.assertEqual(failed["failed"], 1)
        scan.refresh_from_db()
        self.assertEqual(scan.status, MatchupArtifactScanIntent.Status.FAILED)

        with patch(
            "academy.adapters.storage.r2_objects.iter_r2_objects",
            return_value=[{"Key": key}, {"Key": foreign}],
        ), patch("apps.infrastructure.storage.r2.delete_object_r2_storage") as delete, self.captureOnCommitCallbacks(execute=True):
            result = process_artifact_scan_intents(intent_ids=[scan.id])

        self.assertEqual(result["cleaned"], 1)
        scan.refresh_from_db()
        self.assertEqual(scan.status, MatchupArtifactScanIntent.Status.CLEANED)
        self.assertEqual(self._intent_keys(), {key})
        delete.assert_called_once_with(key=key)
        self.assertFalse(SubmissionStorageCleanupIntent.objects.filter(object_key=foreign).exists())

    def test_failed_callback_scan_intent_write_failure_rolls_back_status(self):
        job_id = "job-scan-ledger-outage"
        self.doc.ai_job_id = job_id
        self.doc.save(update_fields=["ai_job_id"])
        AIJobModel.objects.create(
            job_id=job_id, job_type="matchup_analysis", tenant_id=str(self.tenant.id),
            source_domain="matchup", source_id=str(self.doc.id),
        )
        with patch(
            "academy.adapters.storage.r2_objects.iter_r2_objects",
            side_effect=RuntimeError("R2 listing unavailable"),
        ), patch(
            "apps.domains.matchup.analysis_artifacts.schedule_artifact_scan_intent",
            side_effect=RuntimeError("cleanup ledger unavailable"),
        ), pytest.raises(RuntimeError, match="cleanup ledger unavailable"):
            _handle_matchup_ai_result(
                job_id=job_id, status="FAILED", source_id=str(self.doc.id),
                result_payload={}, error="worker failed after upload",
            )

        self.doc.refresh_from_db()
        self.assertEqual(self.doc.status, "processing")
        self.assertFalse(MatchupArtifactScanIntent.objects.filter(tenant=self.tenant).exists())

    def test_cleanup_schedule_failure_rolls_back_auto_replacement(self):
        original = self._problem(
            1, f"tenants/{self.tenant.id}/matchup/legacy/problems/1.png", {},
        )
        with patch(
            "apps.domains.ai.callbacks.schedule_detached_matchup_auto_images",
            side_effect=RuntimeError("cleanup ledger unavailable"),
        ), pytest.raises(RuntimeError, match="cleanup ledger unavailable"):
            _handle_matchup_ai_result(
                job_id="job-without-artifact-list", status="DONE",
                source_id=str(self.doc.id), error=None,
                result_payload={"problems": [{"number": 2, "image_key": "", "meta": {}}]},
            )

        self.assertTrue(self.doc.problems.filter(id=original.id).exists())
        self.assertFalse(self.doc.problems.filter(number=2).exists())
        self.doc.refresh_from_db()
        self.assertEqual(self.doc.status, "processing")

    def test_skeleton_cleanup_failure_keeps_previous_auto_row(self):
        from academy.application.use_cases.ai.pipelines.matchup_pipeline import _insert_skeleton_problems

        original = self._problem(
            1, f"tenants/{self.tenant.id}/matchup/legacy/problems/1.png", {},
        )
        with patch(
            "apps.domains.matchup.analysis_artifacts.schedule_detached_auto_images",
            side_effect=RuntimeError("cleanup ledger unavailable"),
        ), pytest.raises(RuntimeError, match="cleanup ledger unavailable"):
            _insert_skeleton_problems(
                [{"number": 2, "page_index": 0, "bbox": [0, 0, 1, 1]}],
                str(self.doc.id), str(self.tenant.id), "job-skeleton-retry",
            )

        self.assertTrue(self.doc.problems.filter(id=original.id).exists())
        self.assertFalse(self.doc.problems.filter(number=2).exists())

    def test_retry_cleanup_schedule_failure_keeps_previous_auto_row(self):
        original = self._problem(
            1, f"tenants/{self.tenant.id}/matchup/legacy/problems/1.png", {},
        )
        self.doc.status = "failed"
        self.doc.save(update_fields=["status"])
        with patch(
            "apps.domains.matchup.analysis_artifacts.schedule_detached_auto_images",
            side_effect=RuntimeError("cleanup ledger unavailable"),
        ), patch("apps.domains.matchup.services.dispatch_ai_job") as dispatch, pytest.raises(
            RuntimeError, match="cleanup ledger unavailable",
        ):
            retry_document(self.doc, require_failed=True)

        dispatch.assert_not_called()
        self.assertTrue(self.doc.problems.filter(id=original.id).exists())
        self.doc.refresh_from_db()
        self.assertEqual(self.doc.status, "failed")
        self.assertEqual(self.doc.ai_job_id, "")

    def test_provider_failure_retains_job_scoped_intent_for_retry(self):
        from apps.domains.matchup.analysis_artifacts import schedule_unreferenced_analysis_artifacts
        from apps.domains.submissions.services.lifecycle import process_submission_storage_cleanup_intents

        job_id = "job-provider-retry"
        AIJobModel.objects.create(
            job_id=job_id, job_type="matchup_analysis", tenant_id=str(self.tenant.id),
            source_domain="matchup", source_id=str(self.doc.id),
        )
        key = analysis_artifact_prefix(tenant_id=self.tenant.id, job_id=job_id) + "problems/run/1.png"
        with patch(
            "academy.adapters.storage.r2_objects.iter_r2_objects",
            return_value=[{"Key": key}],
        ), patch(
            "apps.infrastructure.storage.r2.delete_object_r2_storage",
            side_effect=RuntimeError("provider unavailable"),
        ), self.captureOnCommitCallbacks(execute=True):
            scheduled = schedule_unreferenced_analysis_artifacts(
                tenant_id=self.tenant.id, document_id=self.doc.id, job_id=job_id,
            )

        self.assertEqual(scheduled, 1)
        intent = SubmissionStorageCleanupIntent.objects.get(tenant=self.tenant, object_key=key)
        self.assertEqual(intent.status, "failed")
        with patch("apps.infrastructure.storage.r2.delete_object_r2_storage") as delete:
            result = process_submission_storage_cleanup_intents(intent_ids=[intent.id])
        self.assertEqual(result.cleaned, 1)
        delete.assert_called_once_with(key=key)
        intent.refresh_from_db()
        self.assertEqual(intent.status, "cleaned")

    def test_forged_job_cannot_claim_another_tenant_namespace(self):
        job_id = "foreign-job"
        AIJobModel.objects.create(
            job_id=job_id, job_type="matchup_analysis", tenant_id=str(self.other_tenant.id),
            source_domain="matchup", source_id=str(self.doc.id),
        )
        key = analysis_artifact_prefix(tenant_id=self.tenant.id, job_id=job_id) + "problems/run/1.png"
        with patch("academy.adapters.storage.r2_objects.iter_r2_objects") as list_objects:
            from apps.domains.matchup.analysis_artifacts import schedule_unreferenced_analysis_artifacts
            self.assertEqual(schedule_unreferenced_analysis_artifacts(
                tenant_id=self.tenant.id, document_id=self.doc.id, job_id=job_id,
            ), 0)
        list_objects.assert_not_called()
        self.assertEqual(self._intent_keys(), set())

    def test_scan_intent_rejects_job_owned_by_another_tenant(self):
        job_id = "other-tenant-job"
        AIJobModel.objects.create(
            job_id=job_id, job_type="matchup_analysis", tenant_id=str(self.other_tenant.id),
            source_domain="matchup", source_id=str(self.doc.id),
        )
        scan = MatchupArtifactScanIntent.objects.create(
            tenant=self.tenant,
            document_id=self.doc.id,
            job_id=job_id,
        )
        with patch("academy.adapters.storage.r2_objects.iter_r2_objects") as list_objects:
            result = process_artifact_scan_intents(intent_ids=[scan.id])
        self.assertEqual(result["failed"], 1)
        list_objects.assert_not_called()
        scan.refresh_from_db()
        self.assertEqual(scan.status, MatchupArtifactScanIntent.Status.FAILED)

    def test_inventory_cascade_rejects_approved_problem(self):
        from apps.domains.inventory.services.deletion import delete_file

        approved = self._problem(
            1, f"tenants/{self.tenant.id}/matchup/legacy/problems/approved.png",
            {"confirmation_status": "confirmed"},
        )
        result = delete_file(
            tenant=self.tenant, file_id=self.doc.inventory_file_id,
            scope="admin", student_ps="",
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "protected_matchup_document")
        self.assertTrue(MatchupProblem.objects.filter(id=approved.id).exists())
        self.assertTrue(InventoryFile.objects.filter(id=self.doc.inventory_file_id).exists())
