from dataclasses import replace
from datetime import timedelta
from unittest.mock import patch

from django.apps import apps
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from apps.core.models import Tenant
from apps.core.r2_paths import ai_submission_key
from apps.domains.submissions.models import Submission
from apps.domains.submissions.services.omr_worker_source import sign_current_omr_source
from apps.shared.contracts.ai_job import AIJob


SIGNER = "apps.domains.submissions.services.omr_worker_source.generate_presigned_get_url"


class OMRWorkerSourceTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(code="omr-source", name="OMR Source")
        self.user = get_user_model().objects.create_user(username="omr-source", tenant=self.tenant)
        self.scan = self.create_scan()
        self.scan.file_key = ai_submission_key(
            tenant_id=self.tenant.pk, submission_id=self.scan.pk, unique_id="original", ext="png",
        )
        self.scan.save(update_fields=["file_key"])
        self.job = self.claim(self.scan)

    def create_scan(self, **fields):
        return Submission.objects.create(
            tenant=self.tenant, user=self.user, target_type=Submission.TargetType.EXAM,
            target_id=42, source=Submission.Source.OMR_SCAN, status=Submission.Status.DISPATCHED,
            **fields,
        )

    def claim(self, scan):
        job = AIJob.new(
            type="omr_grading", source_id=str(scan.pk), source_domain="submissions",
            tenant_id=str(scan.tenant_id), payload={
                "target_id": scan.target_id, "target_type": scan.target_type,
                "submission_id": scan.pk, "file_key": scan.file_key, "download_url": "expired",
            },
        )
        locked_at = timezone.now()
        self.row = apps.get_model("ai_domain", "AIJobModel").objects.create(
            job_id=job.id, job_type=job.type, status="RUNNING", tenant_id=job.tenant_id,
            source_id=job.source_id, source_domain=job.source_domain, payload=job.payload,
            locked_at=locked_at, lease_expires_at=locked_at + timedelta(minutes=55),
        )
        return replace(job, claim_locked_at=locked_at)

    @patch(SIGNER, return_value="renewed")
    def test_worker_download_uses_new_url_and_never_exposes_it_on_failure(self, sign):
        from academy.application.use_cases.ai.pipelines import dispatcher

        with (
            patch.object(dispatcher, "_record_progress"),
            patch.object(dispatcher.AIConfig, "load"),
            patch.object(dispatcher, "download_to_tmp", side_effect=OSError("private-url")) as download,
        ):
            result = dispatcher.handle_ai_job(self.job)
        sign.assert_called_once_with(key=self.scan.file_key, expires_in=3600)
        download.assert_called_once_with(download_url="renewed", job_id=self.job.id)
        self.assertEqual(result.error, "OMR original download failed")
        self.row.refresh_from_db()
        self.assertEqual(self.row.payload["download_url"], "expired")

    @patch(SIGNER, return_value="renewed")
    def test_delayed_and_rotated_jobs_keep_the_owned_original(self, sign):
        # The result callback still owns late hydration after a manual grade.
        Submission.objects.filter(pk=self.scan.pk).update(status=Submission.Status.DONE)
        self.assertEqual(sign_current_omr_source(self.job), "renewed")
        for key in (self.scan.file_key, f"submissions/{self.scan.pk}/historical.png"):
            with self.subTest(key=key):
                self.scan.file_key = key
                self.scan.save(update_fields=["file_key"])
                rotated = self.create_scan(
                    file_key=key, meta={"rotation_rescan": {"source_submission_id": self.scan.pk}},
                    payload={"manual_rotation_degrees": 270},
                )
                self.assertEqual(sign_current_omr_source(self.claim(rotated)), "renewed")
                sign.assert_called_with(key=key, expires_in=3600)
                rotated.refresh_from_db()
                self.assertEqual(rotated.payload["manual_rotation_degrees"], 270)

    @patch(SIGNER)
    def test_mismatched_scope_key_and_claim_never_sign(self, sign):
        job = self.job
        for field, value in (
            ("tenant_id", str(self.tenant.pk + 1)), ("source_id", str(self.scan.pk + 1)),
            ("source_domain", "exams"), ("type", "ocr"), ("id", "another-job"),
            ("claim_locked_at", None), ("claim_locked_at", job.claim_locked_at - timedelta(seconds=1)),
        ):
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                sign_current_omr_source(replace(job, **{field: value}))
        for field, value in (
            ("tenant_id", self.tenant.pk + 1), ("submission_id", self.scan.pk + 1),
            ("target_id", self.scan.target_id + 1), ("target_type", "homework"), ("file_key", "changed"),
        ):
            with self.subTest(field=field), self.assertRaises(ValueError):
                sign_current_omr_source(replace(job, payload={**job.payload, field: value}))
        for bad_key in (
            f"tenants/{self.tenant.pk + 1}/ai/submissions/{self.scan.pk}/other.png",
            f"submissions/{self.scan.pk + 1}/other.png", "unowned.png",
        ):
            Submission.objects.filter(pk=self.scan.pk).update(file_key=bad_key)
            with self.subTest(bad_key=bad_key), self.assertRaises(ValueError):
                sign_current_omr_source(replace(job, payload={**job.payload, "file_key": bad_key}))
        sign.assert_not_called()

    @patch(SIGNER)
    def test_rotated_historical_key_cannot_cross_exam_or_tenant(self, sign):
        key = f"submissions/{self.scan.pk}/historical.png"
        self.scan.file_key = key
        self.scan.save(update_fields=["file_key"])
        rotated = self.create_scan(file_key=key, meta={"rotation_rescan": {"source_submission_id": self.scan.pk}})
        job = self.claim(rotated)
        Submission.objects.filter(pk=self.scan.pk).update(target_id=self.scan.target_id + 1)
        with self.assertRaises(ValueError):
            sign_current_omr_source(job)
        other_tenant = Tenant.objects.create(code="omr-source-foreign", name="Other Tenant")
        Submission.objects.filter(pk=self.scan.pk).update(target_id=self.scan.target_id, tenant=other_tenant)
        with self.assertRaises(ValueError):
            sign_current_omr_source(job)
        sign.assert_not_called()

    @patch(SIGNER)
    def test_discard_supersession_retry_and_finished_claim_never_sign(self, sign):
        for status, meta, error in (
            (Submission.Status.SUPERSEDED, {}, ""),
            (Submission.Status.FAILED, {}, "discarded:duplicate"),
            (Submission.Status.DONE, {"discarded": {"reason": "duplicate"}}, ""),
            (Submission.Status.SUBMITTED, {}, ""),
        ):
            Submission.objects.filter(pk=self.scan.pk).update(status=status, meta=meta, error_message=error)
            with self.subTest(status=status), self.assertRaises(ValueError):
                sign_current_omr_source(self.job)
        Submission.objects.filter(pk=self.scan.pk).update(status=Submission.Status.DISPATCHED)
        self.row.status = "FAILED"
        self.row.save(update_fields=["status"])
        with self.assertRaises(ValueError):
            sign_current_omr_source(self.job)
        self.row.status = "RUNNING"
        self.row.lease_expires_at = timezone.now() - timedelta(seconds=1)
        self.row.save(update_fields=["status", "lease_expires_at"])
        with self.assertRaises(ValueError):
            sign_current_omr_source(self.job)
        self.row.lease_expires_at = timezone.now() + timedelta(minutes=55)
        self.row.save(update_fields=["lease_expires_at"])
        self.claim(self.scan)
        with self.assertRaises(ValueError):
            sign_current_omr_source(self.job)
        sign.assert_not_called()
