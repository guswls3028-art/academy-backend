from __future__ import annotations

from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase
from PIL import Image
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.models import Tenant, TenantMembership
from apps.domains.ai.models import AIJobModel, AIResultModel
from apps.domains.tools.problem_solver.views import (
    TeacherProblemExplanationJobCreateView,
    TeacherProblemExplanationJobStatusView,
)
from apps.shared.contracts.ai_job import AIJob


def _valid_png() -> bytes:
    output = BytesIO()
    Image.new("RGB", (640, 480), "white").save(output, format="PNG")
    return output.getvalue()


class TeacherProblemExplanationViewTests(TestCase):
    def setUp(self):
        cache.clear()
        self.tenant = Tenant.objects.create(
            name="Solver Academy",
            code="solver_academy",
            is_active=True,
        )
        self.user = get_user_model().objects.create_user(
            username="solver_teacher",
            password="test1234",
            tenant=self.tenant,
            is_staff=True,
        )
        TenantMembership.ensure_active(
            tenant=self.tenant,
            user=self.user,
            role="teacher",
        )
        self.factory = APIRequestFactory()

    def _authenticate(self, request, *, user=None, tenant=None):
        request.tenant = tenant or self.tenant
        force_authenticate(request, user=user or self.user)
        return request

    @patch(
        "apps.domains.tools.problem_solver.views.dispatch_tools_ai_job",
        return_value={"ok": True, "job_id": "solver-job-1"},
    )
    @patch("apps.domains.tools.problem_solver.views.upload_fileobj_to_r2_storage")
    def test_create_dispatches_teacher_owned_job_without_exposing_storage_key(
        self,
        upload_file,
        dispatch_job,
    ):
        request = self.factory.post(
            "/api/v1/tools/problem-solver/jobs/",
            {
                "image": SimpleUploadedFile(
                    "problem.png",
                    _valid_png(),
                    content_type="image/png",
                ),
                "subject": "수학",
                "privacy_confirmed": "true",
            },
            format="multipart",
        )

        response = TeacherProblemExplanationJobCreateView.as_view()(
            self._authenticate(request)
        )

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.data, {"job_id": "solver-job-1", "status": "PENDING"})
        self.assertEqual(response["Cache-Control"], "no-store")
        uploaded_key = upload_file.call_args.kwargs["key"]
        self.assertTrue(
            uploaded_key.startswith(
                f"tenants/{self.tenant.id}/tools/problem-solver/tmp/"
            )
        )
        dispatched = dispatch_job.call_args.kwargs
        self.assertEqual(dispatched["job_type"], "teacher_problem_explanation")
        self.assertEqual(dispatched["tenant_id"], str(self.tenant.id))
        self.assertEqual(dispatched["payload"]["request_user_id"], str(self.user.id))
        self.assertEqual(dispatched["payload"]["source_image_key"], uploaded_key)
        self.assertNotIn("source_image_key", response.data)

    @patch("apps.domains.tools.problem_solver.views.upload_fileobj_to_r2_storage")
    def test_create_requires_privacy_confirmation_before_upload(self, upload_file):
        request = self.factory.post(
            "/api/v1/tools/problem-solver/jobs/",
            {
                "image": SimpleUploadedFile(
                    "problem.png",
                    _valid_png(),
                    content_type="image/png",
                ),
            },
            format="multipart",
        )

        response = TeacherProblemExplanationJobCreateView.as_view()(
            self._authenticate(request)
        )

        self.assertEqual(response.status_code, 400)
        upload_file.assert_not_called()

    def test_create_rejects_student_membership(self):
        student = get_user_model().objects.create_user(
            username="solver_student",
            password="test1234",
            tenant=self.tenant,
        )
        TenantMembership.ensure_active(
            tenant=self.tenant,
            user=student,
            role="student",
        )
        request = self.factory.post(
            "/api/v1/tools/problem-solver/jobs/",
            {
                "image": SimpleUploadedFile(
                    "problem.png",
                    _valid_png(),
                    content_type="image/png",
                ),
                "privacy_confirmed": "true",
            },
            format="multipart",
        )

        response = TeacherProblemExplanationJobCreateView.as_view()(
            self._authenticate(request, user=student)
        )

        self.assertEqual(response.status_code, 403)

    def test_status_is_scoped_to_requesting_teacher_and_whitelists_result(self):
        job = AIJobModel.objects.create(
            job_id="solver-done-job",
            job_type="teacher_problem_explanation",
            status="DONE",
            tenant_id=str(self.tenant.id),
            payload={
                "tenant_id": str(self.tenant.id),
                "request_user_id": str(self.user.id),
                "source_image_key": "must-not-leak",
            },
        )
        AIResultModel.objects.create(
            job=job,
            payload={
                "answer": "3",
                "explanation": "양변을 정리하면 3입니다.",
                "answer_check": "대입하면 등식이 성립합니다.",
                "confidence": "high",
                "subject": "수학",
                "provider_debug": "must-not-leak",
            },
        )

        request = self.factory.get(
            "/api/v1/tools/problem-solver/jobs/solver-done-job/"
        )
        response = TeacherProblemExplanationJobStatusView.as_view()(
            self._authenticate(request),
            job_id=job.job_id,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["result"]["review_status"], "teacher_review_required")
        self.assertNotIn("provider_debug", response.data["result"])
        self.assertNotIn("source_image_key", str(response.data))

        other_teacher = get_user_model().objects.create_user(
            username="other_solver_teacher",
            password="test1234",
            tenant=self.tenant,
            is_staff=True,
        )
        TenantMembership.ensure_active(
            tenant=self.tenant,
            user=other_teacher,
            role="teacher",
        )
        other_request = self.factory.get(
            "/api/v1/tools/problem-solver/jobs/solver-done-job/"
        )
        other_response = TeacherProblemExplanationJobStatusView.as_view()(
            self._authenticate(other_request, user=other_teacher),
            job_id=job.job_id,
        )
        self.assertEqual(other_response.status_code, 404)

    def test_failed_status_does_not_expose_provider_error(self):
        job = AIJobModel.objects.create(
            job_id="solver-failed-job",
            job_type="teacher_problem_explanation",
            status="FAILED",
            tenant_id=str(self.tenant.id),
            payload={
                "tenant_id": str(self.tenant.id),
                "request_user_id": str(self.user.id),
            },
            error_message="provider secret response",
            last_error="internal model trace",
        )
        request = self.factory.get(
            "/api/v1/tools/problem-solver/jobs/solver-failed-job/"
        )

        response = TeacherProblemExplanationJobStatusView.as_view()(
            self._authenticate(request),
            job_id=job.job_id,
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("다시 시도", response.data["error"])
        self.assertNotIn("provider", str(response.data))
        self.assertNotIn("trace", str(response.data))


class TeacherProblemExplanationWorkerTests(SimpleTestCase):
    @patch("apps.infrastructure.storage.r2.delete_object_r2_storage")
    def test_publish_failure_cleanup_deletes_only_owned_temp_key(self, delete_source):
        from apps.domains.ai.gateway import _cleanup_publish_failure_artifact

        job = AIJob.new(
            type="teacher_problem_explanation",
            tenant_id="7",
            source_domain="tools_problem_solver",
            payload={
                "tenant_id": "7",
                "source_image_key": (
                    "tenants/7/tools/problem-solver/tmp/test/problem.png"
                ),
            },
        )

        _cleanup_publish_failure_artifact(job)

        delete_source.assert_called_once_with(
            key="tenants/7/tools/problem-solver/tmp/test/problem.png"
        )

    @patch(
        "academy.application.use_cases.ai.pipelines.teacher_problem_explanation.delete_object_r2_storage"
    )
    @patch(
        "academy.application.use_cases.ai.pipelines.teacher_problem_explanation.cleanup_tmp_for_path"
    )
    @patch(
        "academy.application.use_cases.ai.pipelines.teacher_problem_explanation.generate_transcribed_explanations",
        return_value=[{
            "answer": "②",
            "explanation": "조건을 순서대로 적용하면 ②입니다.",
            "answer_check": "조건을 모두 만족합니다.",
            "confidence": "high",
        }],
    )
    @patch(
        "academy.application.use_cases.ai.pipelines.teacher_problem_explanation.transcribe_problem_image",
        return_value="다음 중 옳은 것은?",
    )
    @patch(
        "academy.application.use_cases.ai.pipelines.teacher_problem_explanation.consume_ai_quota"
    )
    def test_worker_returns_review_draft_and_deletes_source(
        self,
        consume_quota,
        transcribe,
        generate,
        cleanup_tmp,
        delete_source,
    ):
        from academy.application.use_cases.ai.pipelines.teacher_problem_explanation import (
            handle_teacher_problem_explanation_job,
        )

        with TemporaryDirectory() as temp_dir:
            image_path = Path(temp_dir) / "problem.png"
            image_path.write_bytes(_valid_png())
            with patch(
                "academy.application.use_cases.ai.pipelines.teacher_problem_explanation.download_r2_key_to_tmp",
                return_value=str(image_path),
            ):
                job = AIJob.new(
                    type="teacher_problem_explanation",
                    tenant_id="7",
                    source_domain="tools_problem_solver",
                    payload={
                        "tenant_id": "7",
                        "request_user_id": "11",
                        "source_image_key": (
                            "tenants/7/tools/problem-solver/tmp/test/problem.png"
                        ),
                        "content_type": "image/png",
                        "subject": "과학",
                    },
                )
                result = handle_teacher_problem_explanation_job(job)

        self.assertEqual(result.status, "DONE", result.error)
        self.assertEqual(result.result["review_status"], "teacher_review_required")
        self.assertNotIn("transcription", result.result)
        consume_quota.assert_called_once_with(kind="problem_studio_transcription")
        transcribe.assert_called_once()
        generate.assert_called_once()
        cleanup_tmp.assert_called_once_with(str(image_path))
        delete_source.assert_called_once_with(
            key="tenants/7/tools/problem-solver/tmp/test/problem.png"
        )

    @patch(
        "academy.application.use_cases.ai.pipelines.teacher_problem_explanation.delete_object_r2_storage"
    )
    def test_worker_rejects_cross_tenant_source_without_deleting_it(self, delete_source):
        from academy.application.use_cases.ai.pipelines.teacher_problem_explanation import (
            handle_teacher_problem_explanation_job,
        )

        job = AIJob.new(
            type="teacher_problem_explanation",
            tenant_id="7",
            source_domain="tools_problem_solver",
            payload={
                "tenant_id": "8",
                "request_user_id": "11",
                "source_image_key": (
                    "tenants/8/tools/problem-solver/tmp/test/problem.png"
                ),
                "content_type": "image/png",
            },
        )

        result = handle_teacher_problem_explanation_job(job)

        self.assertEqual(result.status, "FAILED")
        self.assertEqual(result.error, "tenant_id mismatch")
        delete_source.assert_not_called()
