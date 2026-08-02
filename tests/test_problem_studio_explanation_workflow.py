from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.models import Tenant, TenantMembership
from apps.domains.ai.callbacks import dispatch_ai_result_to_domain
from apps.domains.ai.models import AIJobModel
from apps.domains.tools.problem_studio.beta_access import reserve_beta_run
from apps.domains.tools.problem_studio.explanation_workflow import (
    _solve_step,
    resume_explanation_workflow,
    start_explanation_workflow,
)
from apps.domains.tools.problem_studio.models import ProblemStudioBetaRun
from apps.domains.tools.problem_studio.views import (
    ProblemStudioExplanationRunCreateView,
    ProblemStudioExplanationRunStatusView,
)
from apps.shared.contracts.ai_job import AIJob


pytestmark = pytest.mark.django_db


@pytest.fixture
def explanation_tenant_user():
    tenant = Tenant.objects.create(
        name="Problem Studio Explanation",
        code="problem_studio_explanation",
        is_active=True,
    )
    user = get_user_model().objects.create_user(
        username="problem_studio_explanation_owner",
        password="test1234",
        tenant=tenant,
        is_staff=True,
    )
    TenantMembership.ensure_active(tenant=tenant, user=user, role="owner")
    return tenant, user


def test_explanation_run_create_reserves_one_tenant_credit(explanation_tenant_user):
    tenant, user = explanation_tenant_user
    request = APIRequestFactory().post(
        "/api/v1/tools/problem-studio/explanation-runs/",
        {
            "payload": json.dumps({"subject": "통합과학", "note_policy": "짧게 설명"}),
            "source_files": SimpleUploadedFile(
                "science.pdf",
                b"%PDF-1.4\n%%EOF",
                content_type="application/pdf",
            ),
        },
        format="multipart",
    )
    request.tenant = tenant
    force_authenticate(request, user=user)

    with (
        patch("apps.infrastructure.storage.r2.upload_fileobj_to_r2_storage"),
        patch(
            "apps.domains.tools.problem_studio.views.start_explanation_workflow",
            return_value="child-job",
        ),
    ):
        response = ProblemStudioExplanationRunCreateView.as_view()(request)

    assert response.status_code == 202
    run = ProblemStudioBetaRun.objects.get(pk=response.data["run_id"])
    assert run.tenant_id == tenant.id
    assert run.requested_by_id == user.id
    assert run.source_name == "science.pdf"
    assert run.source_archive_key.startswith(
        f"tenants/{tenant.id}/tools/problem-studio/tmp/explanation-runs/{run.id}/"
    )
    assert run.request_payload == {"subject": "통합과학", "note_policy": "짧게 설명"}
    assert response.data["beta_access"]["remaining_runs"] == 2
    assert response["Cache-Control"] == "no-store"


def test_explanation_run_status_is_scoped_to_requesting_teacher(explanation_tenant_user):
    tenant, owner = explanation_tenant_user
    other = get_user_model().objects.create_user(
        username="problem_studio_explanation_other",
        password="test1234",
        tenant=tenant,
        is_staff=True,
    )
    TenantMembership.ensure_active(tenant=tenant, user=other, role="teacher")
    run = reserve_beta_run(tenant=tenant, user=owner)

    request = APIRequestFactory().get(f"/api/v1/tools/problem-studio/explanation-runs/{run.id}/")
    request.tenant = tenant
    force_authenticate(request, user=other)
    response = ProblemStudioExplanationRunStatusView.as_view()(request, run_id=run.id)

    assert response.status_code == 404


def test_start_and_resume_bind_idempotent_child_jobs(explanation_tenant_user):
    tenant, user = explanation_tenant_user
    run = reserve_beta_run(tenant=tenant, user=user)
    run.source_archive_key = (
        f"tenants/{tenant.id}/tools/problem-studio/tmp/explanation-runs/{run.id}/sources.zip"
    )
    run.save(update_fields=["source_archive_key", "updated_at"])

    with patch(
        "apps.domains.tools.problem_studio.explanation_workflow.dispatch_tools_ai_job",
        side_effect=[
            {"ok": True, "job_id": "extract-job"},
            {"ok": True, "job_id": "resume-job"},
        ],
    ) as dispatch:
        assert start_explanation_workflow(
            run_id=str(run.id),
            tenant_id=str(tenant.id),
        ) == "extract-job"
        run.refresh_from_db()
        assert run.job_id == "extract-job"
        run.status = ProblemStudioBetaRun.Status.RELEASED
        run.stage = ProblemStudioBetaRun.Stage.SOLVE
        run.job_id = ""
        run.checkpoint_key = f"tenants/{tenant.id}/tools/problem-studio/tmp/explanation-runs/{run.id}/checkpoint.zip"
        run.solutions_key = f"tenants/{tenant.id}/tools/problem-studio/tmp/explanation-runs/{run.id}/solutions.json"
        run.save()

        assert resume_explanation_workflow(run_id=str(run.id), tenant=tenant, user=user) == "resume-job"

    run.refresh_from_db()
    assert run.status == ProblemStudioBetaRun.Status.RESERVED
    assert run.job_id == "resume-job"
    assert dispatch.call_args_list[0].kwargs["idempotency_key"].endswith(":extract:0")
    assert dispatch.call_args_list[1].kwargs["idempotency_key"].endswith(":solve:0")
    assert dispatch.call_args_list[1].kwargs["force_rerun"] is True


def test_worker_terminal_failure_releases_run_for_resume(explanation_tenant_user):
    tenant, user = explanation_tenant_user
    run = reserve_beta_run(tenant=tenant, user=user)
    run.stage = ProblemStudioBetaRun.Stage.SOLVE
    run.job_id = "timed-out-step"
    run.source_archive_key = f"tenants/{tenant.id}/tools/problem-studio/tmp/explanation-runs/{run.id}/sources.zip"
    run.save()
    AIJobModel.objects.create(
        job_id="timed-out-step",
        job_type="problem_studio_transcription",
        tenant_id=str(tenant.id),
        source_domain="tools_problem_studio",
        source_id=str(run.id),
        status="FAILED",
        payload={
            "tenant_id": str(tenant.id),
            "request_user_id": str(user.id),
            "explanation_run_id": str(run.id),
            "explanation_stage": "solve",
        },
    )

    handled = dispatch_ai_result_to_domain(
        job_id="timed-out-step",
        status="FAILED",
        result_payload={},
        error="inference_timeout_60min",
        source_domain="tools_problem_studio",
        source_id=str(run.id),
    )

    run.refresh_from_db()
    assert handled is True
    assert run.status == ProblemStudioBetaRun.Status.RELEASED
    assert run.stage == ProblemStudioBetaRun.Stage.SOLVE
    assert run.job_id == ""
    assert "inference_timeout_60min" in run.last_error


def test_solve_step_processes_only_one_batch_and_schedules_continuation(
    explanation_tenant_user,
    tmp_path,
):
    tenant, user = explanation_tenant_user
    run = reserve_beta_run(tenant=tenant, user=user)
    run.stage = ProblemStudioBetaRun.Stage.SOLVE
    run.job_id = "solve-job"
    run.question_count = 20
    run.checkpoint_key = f"tenants/{tenant.id}/tools/problem-studio/tmp/explanation-runs/{run.id}/checkpoint.zip"
    run.solutions_key = f"tenants/{tenant.id}/tools/problem-studio/tmp/explanation-runs/{run.id}/solutions.json"
    run.request_payload = {"subject": "통합과학", "note_policy": "핵심만"}
    run.save()
    job = AIJob(
        id="solve-job",
        type="problem_studio_transcription",
        payload={"tenant_id": str(tenant.id)},
        tenant_id=str(tenant.id),
        source_domain="tools_problem_studio",
        source_id=str(run.id),
    )

    def fake_restore(**kwargs):
        root = kwargs["root"] / "work"
        root.mkdir(parents=True, exist_ok=True)
        (root / "manifest.json").write_text('{"items": []}', encoding="utf-8")
        (root / "solutions.json").write_text('{"items": {}}', encoding="utf-8")
        return root

    def fake_solve(**kwargs):
        assert kwargs["limit"] == 10
        assert kwargs["batch_size"] == 10
        state = {
            "items": {
                str(number): {
                    "answer_source": "source_reference",
                    "verification_status": "",
                }
                for number in range(1, 11)
            }
        }
        (kwargs["work_dir"] / "solutions.json").write_text(
            json.dumps(state),
            encoding="utf-8",
        )
        return state

    with (
        patch(
            "apps.domains.tools.problem_studio.explanation_workflow._restore_work_dir",
            side_effect=fake_restore,
        ),
        patch("scripts.problem_studio_pdf_prototype.solve_manifest", side_effect=fake_solve),
        patch("apps.domains.tools.problem_studio.explanation_workflow._upload_path"),
        patch("apps.domains.tools.problem_studio.explanation_workflow._schedule_next") as schedule,
        patch(
            "academy.adapters.ai.config.AIConfig.load",
            return_value=SimpleNamespace(
                PROBLEM_GEN_BEDROCK_MODEL="global.amazon.nova-2-lite-v1:0",
                BEDROCK_REGION="ap-northeast-2",
            ),
        ),
    ):
        result = _solve_step(run=run, job=job)

    assert result == {"completed": 10, "question_count": 20, "next_stage": "solve"}
    assert schedule.call_args.kwargs["stage"] == ProblemStudioBetaRun.Stage.SOLVE
    assert schedule.call_args.kwargs["cursor"] == 10
