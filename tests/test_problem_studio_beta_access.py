from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.models import Tenant, TenantMembership
from apps.domains.ai.callbacks import dispatch_ai_result_to_domain
from apps.domains.ai.models import AIJobModel
from apps.domains.tools.problem_studio.beta_access import (
    ProblemStudioBetaLimitReached,
    beta_access_snapshot,
    beta_run_id_from_job_payload,
    bind_beta_run,
    reserve_beta_run,
    settle_beta_run,
)
from apps.domains.tools.problem_studio.models import ProblemStudioBetaRun
from apps.domains.tools.problem_studio.views import ProblemStudioBetaAccessView


pytestmark = pytest.mark.django_db


@pytest.fixture
def beta_tenant_user():
    tenant = Tenant.objects.create(
        name="Problem Studio Beta",
        code="problem_studio_beta",
        is_active=True,
    )
    user = get_user_model().objects.create_user(
        username="problem_studio_beta_owner",
        password="test1234",
        tenant=tenant,
        is_staff=True,
    )
    TenantMembership.ensure_active(tenant=tenant, user=user, role="owner")
    return tenant, user


def _job_for_run(*, tenant, user, run, job_id):
    job = AIJobModel.objects.create(
        job_id=job_id,
        job_type="problem_studio_transcription",
        tenant_id=str(tenant.id),
        source_domain="tools_problem_studio",
        status="RUNNING",
        payload={
            "tenant_id": str(tenant.id),
            "request_user_id": str(user.id),
            "problem_studio_payload": {
                "beta": {"run_id": str(run.id), "label": "Beta"},
            },
        },
    )
    bind_beta_run(run=run, job_id=job_id)
    return job


def test_beta_trial_allows_three_tenant_runs(beta_tenant_user):
    tenant, user = beta_tenant_user

    for _index in range(3):
        reserve_beta_run(tenant=tenant, user=user)

    snapshot = beta_access_snapshot(tenant=tenant)
    assert snapshot["free_run_limit"] == 3
    assert snapshot["reserved_runs"] == 3
    assert snapshot["remaining_runs"] == 0
    assert snapshot["can_start"] is False
    other_user = get_user_model().objects.create_user(
        username="problem_studio_beta_second_teacher",
        password="test1234",
        tenant=tenant,
        is_staff=True,
    )
    TenantMembership.ensure_active(tenant=tenant, user=other_user, role="teacher")
    with pytest.raises(ProblemStudioBetaLimitReached):
        reserve_beta_run(tenant=tenant, user=other_user)


def test_beta_access_endpoint_returns_tenant_balance(beta_tenant_user):
    tenant, user = beta_tenant_user
    reserve_beta_run(tenant=tenant, user=user)
    request = APIRequestFactory().get("/api/v1/tools/problem-studio/beta-access/")
    request.tenant = tenant
    force_authenticate(request, user=user)

    response = ProblemStudioBetaAccessView.as_view()(request)

    assert response.status_code == 200
    assert response.data["beta_access"]["label"] == "Beta"
    assert response.data["beta_access"]["remaining_runs"] == 2
    assert response["Cache-Control"] == "no-store"

def test_failed_beta_job_returns_credit_and_done_job_consumes_it(beta_tenant_user):
    tenant, user = beta_tenant_user
    failed_run = reserve_beta_run(tenant=tenant, user=user)
    _job_for_run(
        tenant=tenant,
        user=user,
        run=failed_run,
        job_id="problem-studio-beta-failed",
    )

    failed_run_payload = AIJobModel.objects.get(
        job_id="problem-studio-beta-failed",
    ).payload
    assert failed_run_payload["problem_studio_payload"]["beta"]["label"] == "Beta"
    settle_beta_run(
        run_id=beta_run_id_from_job_payload(failed_run_payload),
        job_id="problem-studio-beta-failed",
        terminal_status="FAILED",
        error="provider unavailable",
    )
    failed_run.refresh_from_db()
    assert failed_run.status == ProblemStudioBetaRun.Status.RELEASED
    assert beta_access_snapshot(tenant=tenant)["remaining_runs"] == 3

    completed_run = reserve_beta_run(tenant=tenant, user=user)
    _job_for_run(
        tenant=tenant,
        user=user,
        run=completed_run,
        job_id="problem-studio-beta-done",
    )
    settle_beta_run(
        run_id=str(completed_run.id),
        job_id="problem-studio-beta-done",
        terminal_status="DONE",
    )
    settle_beta_run(
        run_id=str(completed_run.id),
        job_id="problem-studio-beta-done",
        terminal_status="DONE",
    )

    completed_run.refresh_from_db()
    snapshot = beta_access_snapshot(tenant=tenant)
    assert completed_run.status == ProblemStudioBetaRun.Status.COMPLETED
    assert snapshot["completed_runs"] == 1
    assert snapshot["reserved_runs"] == 0
    assert snapshot["remaining_runs"] == 2


def test_problem_studio_terminal_callback_settles_nested_beta_run(beta_tenant_user):
    tenant, user = beta_tenant_user
    run = reserve_beta_run(tenant=tenant, user=user)
    _job_for_run(
        tenant=tenant,
        user=user,
        run=run,
        job_id="problem-studio-beta-callback",
    )

    handled = dispatch_ai_result_to_domain(
        job_id="problem-studio-beta-callback",
        status="DONE",
        result_payload={},
        error=None,
        source_domain="tools_problem_studio",
        source_id=None,
    )

    run.refresh_from_db()
    assert handled is True
    assert run.status == ProblemStudioBetaRun.Status.COMPLETED
