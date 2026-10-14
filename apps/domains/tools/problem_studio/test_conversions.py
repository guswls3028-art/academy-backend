from __future__ import annotations

import copy
import io
import json
import tempfile
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.models import Tenant, TenantMembership
from apps.domains.tools.problem_studio.conversion_views import ConversionCollectionView, ConversionDetailView, ConversionActionView
from apps.domains.tools.problem_studio.conversion_worker import handle_conversion_job
from apps.domains.tools.problem_studio.conversions import serialize_conversion, validate_question
from apps.domains.tools.problem_studio.models import ProblemStudioConversion
from apps.shared.contracts.ai_job import AIJob


class ConversionJourneyTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Conversion QA", code="qa-conversion")
        self.user = get_user_model().objects.create_user(username="qa-conversion", tenant=self.tenant, is_staff=True)
        TenantMembership.ensure_active(tenant=self.tenant, user=self.user, role="owner")
        self.storage = {}
        self.jobs = []
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)

        def upload(*, fileobj, key, content_type, **kwargs):
            self.storage[key] = fileobj.read()

        def download(*, r2_key, job_id):
            target = Path(self.temp.name) / f"asset-{len(list(Path(self.temp.name).iterdir()))}"
            target.write_bytes(self.storage[r2_key])
            return str(target)

        def dispatch(**kwargs):
            job = AIJob.new(type=kwargs["job_type"], tenant_id=kwargs["tenant_id"], payload=kwargs["payload"])
            self.jobs.append(job)
            return {"ok": True, "job_id": job.id}

        for target, replacement in (
            ("apps.infrastructure.storage.r2.upload_fileobj_to_r2_storage", upload),
            ("apps.infrastructure.storage.r2.generate_presigned_get_url_storage", lambda **kwargs: "https://storage.example/" + kwargs["key"]),
            ("academy.adapters.ai.storage.downloader.download_r2_key_to_tmp", download),
            ("academy.adapters.ai.storage.downloader.cleanup_tmp_for_path", lambda path: Path(path).unlink(missing_ok=True)),
            ("apps.domains.tools.problem_studio.conversions.dispatch_tools_ai_job", dispatch),
        ):
            patcher = patch(target, side_effect=replacement)
            patcher.start()
            self.addCleanup(patcher.stop)

    def request(self, method, data=None, *, user=None, tenant=None, multipart=False):
        request = getattr(APIRequestFactory(), method)("/api/v1/tools/problem-studio/conversions/", data or {}, format="multipart" if multipart else "json")
        request.tenant = tenant or self.tenant
        force_authenticate(request, user=user or self.user)
        return request

    def create(self, title="합성 시험지"):
        from reportlab.pdfgen.canvas import Canvas
        output = io.BytesIO()
        canvas = Canvas(output)
        text = canvas.beginText(50, 760)
        for line in ["1. Find the sum of 2 and 3.", "(1) 4", "(2) 5", "", "2. Find the product of 2 and 4.", "(1) 6", "(2) 8"]:
            text.textLine(line)
        canvas.drawText(text)
        canvas.save()
        pdf = output.getvalue()
        with self.captureOnCommitCallbacks(execute=True):
            response = ConversionCollectionView.as_view()(self.request("post", {
                "payload": json.dumps({"title": title, "options": {"level": "questions", "external_ai_confirmed": True}}),
                "source_files": SimpleUploadedFile("qa-exam.pdf", pdf, content_type="application/pdf"),
            }, multipart=True))
        self.assertEqual(response.status_code, 202, response.data)
        return ProblemStudioConversion.objects.get(id=response.data["id"])

    def test_upload_convert_review_edit_rebuild_reload_and_download(self):
        row = self.create()
        result = handle_conversion_job(self.jobs[-1])
        self.assertEqual(result.status, "DONE", result.error)
        row.refresh_from_db()
        self.assertEqual(len(row.document["questions"]), 2)
        self.assertEqual(set(row.artifacts), {"manuscript", "arranged", "a3"})
        question = row.document["questions"][0]
        response = ConversionDetailView.as_view()(self.request("patch", {
            "version": row.version, "question_id": question["id"],
            "question": {"prompt": "Teacher revised question", "answer": "5", "explanation": "2 + 3 = 5"},
        }), conversion_id=row.id)
        self.assertEqual(response.status_code, 200, response.data)
        row.refresh_from_db()
        self.assertNotEqual(row.version, row.artifact_version)
        self.assertEqual(row.document["questions"][0]["prompt"], "Teacher revised question")
        with self.captureOnCommitCallbacks(execute=True):
            response = ConversionActionView.as_view()(self.request("post", {"version": row.version, "action": "rebuild"}), conversion_id=row.id)
        self.assertEqual(response.status_code, 202)
        self.assertEqual(handle_conversion_job(self.jobs[-1]).status, "DONE")
        row.refresh_from_db()
        self.assertEqual(row.version, row.artifact_version)
        for asset in row.artifacts.values():
            with zipfile.ZipFile(io.BytesIO(self.storage[asset["key"]])) as archive:
                self.assertIn("Teacher revised question", archive.read("Preview/PrvText.txt").decode())
        loaded = ConversionDetailView.as_view()(self.request("get"), conversion_id=row.id)
        self.assertNotIn("key", loaded.data["artifacts"]["manuscript"])
        self.assertIn("url", loaded.data["artifacts"]["manuscript"])
        self.assertNotIn("source_archive_key", loaded.data)

    def test_other_teacher_and_other_tenant_cannot_read_or_change(self):
        row = self.create()
        other = get_user_model().objects.create_user(username="qa-other", tenant=self.tenant, is_staff=True)
        TenantMembership.ensure_active(tenant=self.tenant, user=other, role="teacher")
        response = ConversionDetailView.as_view()(self.request("get", user=other), conversion_id=row.id)
        self.assertEqual(response.status_code, 404)
        other_tenant = Tenant.objects.create(name="Other", code="qa-other")
        response = ConversionDetailView.as_view()(self.request("get", tenant=other_tenant), conversion_id=row.id)
        self.assertEqual(response.status_code, 403)
        forged = AIJob.new(type="problem_studio_transcription", tenant_id=str(other_tenant.id), payload=self.jobs[-1].payload)
        self.assertEqual(handle_conversion_job(forged).status, "FAILED")

    def test_version_conflict_keeps_teacher_content(self):
        row = self.create()
        self.assertEqual(handle_conversion_job(self.jobs[-1]).status, "DONE")
        row.refresh_from_db()
        original = copy.deepcopy(row.document)
        response = ConversionDetailView.as_view()(self.request("patch", {"version": 0, "question_id": "q1", "question": {"prompt": "stale"}}), conversion_id=row.id)
        self.assertEqual(response.status_code, 409)
        row.refresh_from_db()
        self.assertEqual(row.document, original)

    def test_ai_correction_is_a_proposal_until_explicitly_applied(self):
        row = self.create()
        self.assertEqual(handle_conversion_job(self.jobs[-1]).status, "DONE")
        row.refresh_from_db()
        original = row.document["questions"][0]["prompt"]
        with self.captureOnCommitCallbacks(execute=True):
            ConversionActionView.as_view()(self.request("post", {"version": row.version, "action": "correct", "question_id": "q1", "instruction": "고쳐 주세요"}), conversion_id=row.id)
        with patch("apps.domains.tools.problem_studio.conversion_ai.propose_question", return_value={"prompt": "AI proposal", "choices": [], "answer": "5", "explanation": "검수할 해설"}):
            self.assertEqual(handle_conversion_job(self.jobs[-1]).status, "DONE")
        row.refresh_from_db()
        self.assertEqual(row.document["questions"][0]["prompt"], original)
        self.assertEqual(row.document["questions"][0]["proposal"]["prompt"], "AI proposal")
        response = ConversionDetailView.as_view()(self.request("patch", {"version": row.version, "question_id": "q1", "action": "apply_proposal"}), conversion_id=row.id)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["document"]["questions"][0]["prompt"], "AI proposal")

    def test_failed_export_resumes_without_reextracting_source(self):
        row = self.create()
        with patch("apps.domains.tools.problem_studio.conversion_worker._build", side_effect=RuntimeError("synthetic writer failure")):
            self.assertEqual(handle_conversion_job(self.jobs[-1]).status, "FAILED")
        row.refresh_from_db()
        self.assertEqual(row.status, "FAILED")
        self.assertEqual(len(row.document["questions"]), 2)
        with self.captureOnCommitCallbacks(execute=True):
            response = ConversionActionView.as_view()(self.request("post", {"version": row.version, "action": "resume"}), conversion_id=row.id)
        self.assertEqual(response.status_code, 202)
        with patch("apps.domains.tools.problem_studio.conversion_worker._extract", side_effect=AssertionError("must reuse checkpoint")):
            self.assertEqual(handle_conversion_job(self.jobs[-1]).status, "DONE")

    def test_uncertain_review_gate_and_invalid_crop(self):
        from rest_framework.exceptions import ValidationError
        with self.assertRaises(ValidationError):
            validate_question({"crop": [0.9, 0, 0.5, 1]}, {})
        with self.assertRaises(ValidationError):
            validate_question({"crop": [0, 0, float("nan"), 1]}, {})
        row = self.create()
        row.options["require_review"] = True
        row.save(update_fields=["options"])
        self.assertEqual(handle_conversion_job(self.jobs[-1]).status, "DONE")
        row.refresh_from_db()
        self.assertFalse(row.artifacts)
        response = ConversionDetailView.as_view()(self.request("patch", {"version": row.version, "action": "accept_all"}), conversion_id=row.id)
        self.assertEqual(response.status_code, 200)
        row.refresh_from_db()
        with self.captureOnCommitCallbacks(execute=True):
            ConversionActionView.as_view()(self.request("post", {"version": row.version, "action": "rebuild"}), conversion_id=row.id)
        self.assertEqual(handle_conversion_job(self.jobs[-1]).status, "DONE")
        row.refresh_from_db()
        self.assertEqual(len(row.artifacts), 3)

    def test_serialization_rejects_foreign_storage_keys(self):
        row = self.create()
        row.artifacts = {"manuscript": {"key": "tenants/foreign/private.hwpx"}}
        row.artifact_version = row.version
        self.assertIsNone(serialize_conversion(row)["artifacts"]["manuscript"])

    def test_history_has_a_next_page_without_exposing_another_owner(self):
        ProblemStudioConversion.objects.bulk_create([
            ProblemStudioConversion(tenant=self.tenant, owner=self.user, title=f"시험지 {i}", source_archive_key="synthetic")
            for i in range(61)
        ])
        response = ConversionCollectionView.as_view()(self.request("get"))
        self.assertEqual(len(response.data["conversions"]), 60)
        self.assertEqual(response.data["next_offset"], 60)
        response = ConversionCollectionView.as_view()(self.request("get", {"offset": 60}))
        self.assertEqual(len(response.data["conversions"]), 1)
        self.assertIsNone(response.data["next_offset"])

    def test_resume_preserves_question_manually_edited_after_failure(self):
        row = self.create()
        self.assertEqual(handle_conversion_job(self.jobs[-1]).status, "DONE")
        row.refresh_from_db()
        row.status = "FAILED"
        row.options["level"] = "explanations"
        for question in row.document["questions"]:
            question["analysis_complete"] = False
        row.save()
        response = ConversionDetailView.as_view()(self.request("patch", {
            "version": row.version, "question_id": "q1", "question": {"answer": "teacher answer", "explanation": "teacher solution"},
        }), conversion_id=row.id)
        self.assertEqual(response.status_code, 200)
        row.refresh_from_db()
        self.assertTrue(row.document["questions"][0]["analysis_complete"])
        row.status = "FAILED"
        row.save(update_fields=["status"])
        with self.captureOnCommitCallbacks(execute=True):
            ConversionActionView.as_view()(self.request("post", {"version": row.version, "action": "resume"}), conversion_id=row.id)
        with patch("apps.domains.tools.problem_studio.conversion_ai.propose_question", return_value={"prompt": "second", "choices": [], "answer": "8", "explanation": "2 * 4", "review_flags": []}) as propose:
            self.assertEqual(handle_conversion_job(self.jobs[-1]).status, "DONE")
        self.assertEqual(propose.call_count, 1)
        row.refresh_from_db()
        self.assertEqual(row.document["questions"][0]["answer"], "teacher answer")
        self.assertEqual(row.document["questions"][0]["explanation"], "teacher solution")

    def test_downloads_are_not_resigned_for_stale_versions(self):
        row = self.create()
        self.assertEqual(handle_conversion_job(self.jobs[-1]).status, "DONE")
        row.refresh_from_db()
        row.version += 1
        self.assertEqual(serialize_conversion(row)["artifacts"], {})

    def test_blank_title_uses_the_original_filename(self):
        self.assertEqual(self.create("   ").title, "qa-exam")

    def test_failed_worker_readback_is_readonly_and_explicit_edit_recovers(self):
        row = self.create()
        self.assertEqual(handle_conversion_job(self.jobs[-1]).status, "DONE")
        row.refresh_from_db()
        row.status = "RUNNING"
        row.save(update_fields=["status"])
        with patch("academy.adapters.db.django.repositories_ai.get_job_model_for_status", return_value=SimpleNamespace(status="FAILED")):
            response = ConversionDetailView.as_view()(self.request("get"), conversion_id=row.id)
            self.assertEqual(response.data["status"], "FAILED")
            row.refresh_from_db()
            self.assertEqual(row.status, "RUNNING")
            response = ConversionDetailView.as_view()(self.request("patch", {
                "version": row.version, "question_id": "q1", "question": {"prompt": "Recovered teacher edit"},
            }), conversion_id=row.id)
        self.assertEqual(response.status_code, 200)
        row.refresh_from_db()
        self.assertEqual(row.document["questions"][0]["prompt"], "Recovered teacher edit")

    def test_teacher_delete_is_restricted_but_owned_tenant_cleanup_can_cascade(self):
        from django.db.models.deletion import RestrictedError
        row = self.create()
        with self.assertRaises(RestrictedError):
            self.user.delete()
        self.tenant.delete()
        self.assertFalse(ProblemStudioConversion.objects.filter(id=row.id).exists())

    def test_crop_export_preserves_aspect_ratio_and_reanalysis_receives_full_page(self):
        from PIL import Image
        from apps.domains.tools.problem_studio.conversion_worker import _question_image
        from apps.domains.tools.problem_studio.hwpx_writer import build_hwpx_exam_document
        row = self.create()
        self.assertEqual(handle_conversion_job(self.jobs[-1]).status, "DONE")
        row.refresh_from_db()
        row.document["questions"][0].update(page_index=0, crop=[0, 0, 0.5, 0.1])
        row.save(update_fields=["document"])
        page_data, _ = _question_image(row, row.document["questions"][0], prefer_page=True)
        crop_data, _ = _question_image(row, row.document["questions"][0])
        with Image.open(io.BytesIO(page_data)) as page, Image.open(io.BytesIO(crop_data)) as crop:
            self.assertGreater(page.height, crop.height)
            expected = crop.size
        with self.captureOnCommitCallbacks(execute=True):
            ConversionActionView.as_view()(self.request("post", {"version": row.version, "action": "rebuild"}), conversion_id=row.id)
        with patch("apps.domains.tools.problem_studio.hwpx_writer.build_hwpx_exam_document", wraps=build_hwpx_exam_document) as build:
            self.assertEqual(handle_conversion_job(self.jobs[-1]).status, "DONE")
        visual = build.call_args.kwargs["question_visuals"][1]
        self.assertEqual((visual["width_px"], visual["height_px"]), expected)
