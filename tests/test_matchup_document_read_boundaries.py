import json
from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model
from django.test import Client, RequestFactory, override_settings
from django.utils import timezone
from rest_framework_simplejwt.tokens import AccessToken

from apps.core.middleware.safe_method_write import SafeMethodDatabaseWriteMiddleware
from apps.core.models import Tenant, TenantMembership
from apps.domains.ai.models import AIJobModel, AIResultModel
from apps.domains.inventory.models import InventoryFile
from apps.domains.matchup import views
from apps.domains.matchup.models import (
    MatchupArtifactScanIntent,
    MatchupDocument,
    MatchupHitReport,
    MatchupProblem,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def scenario():
    tenant = Tenant.objects.create(code="qa-matchup-read", name="Read boundary")
    user = get_user_model().objects.create_user(
        username="qa-matchup-read-staff", tenant=tenant, is_staff=True,
    )
    TenantMembership.objects.create(tenant=tenant, user=user, role="teacher", is_active=True)
    inventory = InventoryFile.objects.create(
        tenant=tenant, scope="admin", display_name="Original exam",
        original_name="exam.pdf", r2_key=f"tenants/{tenant.id}/exam.pdf",
        content_type="application/pdf",
    )
    job = AIJobModel.objects.create(
        job_id="qa-matchup-read-job", job_type="matchup_analysis", status="RUNNING",
        tenant_id=str(tenant.id), tier="basic", source_domain="matchup",
        locked_by="qa-worker", locked_at=timezone.now() - timedelta(hours=1),
        lease_expires_at=timezone.now() - timedelta(minutes=11),
    )
    doc = MatchupDocument.objects.create(
        tenant=tenant, author=user, inventory_file=inventory, title="Original exam",
        r2_key=inventory.r2_key, original_name=inventory.original_name,
        status="processing", ai_job_id=job.job_id, problem_count=1,
        meta={"preserved": "original metadata"},
    )
    job.source_id = str(doc.id)
    job.save(update_fields=["source_id", "updated_at"])
    MatchupProblem.objects.create(
        tenant=tenant, document=doc, number=1, text="User correction",
        image_key=f"tenants/{tenant.id}/manual.png", meta={"manual": True, "approved": True},
    )
    MatchupHitReport.objects.create(
        tenant=tenant, author=user, document=doc, status="submitted", summary="User report",
    )
    return SimpleNamespace(tenant=tenant, user=user, job=job, doc=doc)


@pytest.fixture
def client():
    # Exercise real URL routing, tenant resolution, JWT authentication and the
    # production safe-method guard without unrelated request audit middleware.
    with override_settings(MIDDLEWARE=[
        "apps.core.middleware.safe_method_write.SafeMethodDatabaseWriteMiddleware",
        "apps.core.middleware.tenant.TenantMiddleware",
    ]):
        yield Client()


def _token(scenario):
    token = AccessToken.for_user(scenario.user)
    token["tenant_id"] = scenario.tenant.id
    token["token_version"] = scenario.user.token_version
    return token


def _path(scenario, endpoint):
    if endpoint == "list":
        return "/api/v1/matchup/documents/"
    return f"/api/v1/matchup/documents/{scenario.doc.id}/job/"


def _get(client, scenario, endpoint, token, *, tenant=None):
    headers = {
        "HTTP_HOST": "api.hakwonplus.com",
        "HTTP_X_TENANT_CODE": (tenant or scenario.tenant).code,
    }
    if token is not None:
        headers["HTTP_AUTHORIZATION"] = f"Bearer {token}"
    return client.get(_path(scenario, endpoint), **headers)


def _snapshot():
    models = (
        MatchupDocument, InventoryFile, MatchupProblem, MatchupHitReport,
        AIJobModel, AIResultModel, MatchupArtifactScanIntent,
    )
    return {model: list(model.objects.order_by("pk").values()) for model in models}


def _foreign_document():
    tenant = Tenant.objects.create(code="qa-matchup-foreign", name="Foreign tenant")
    inventory = InventoryFile.objects.create(
        tenant=tenant, scope="admin", original_name="foreign.pdf",
        r2_key=f"tenants/{tenant.id}/foreign.pdf", content_type="application/pdf",
    )
    return MatchupDocument.objects.create(
        tenant=tenant, inventory_file=inventory, title="Foreign document",
        r2_key=inventory.r2_key, original_name=inventory.original_name,
    )


@pytest.mark.parametrize("endpoint", ["list", "job"])
@pytest.mark.parametrize("job_status", [
    "RUNNING", "DONE", "FAILED", "REJECTED_BAD_INPUT", "REVIEW_REQUIRED",
])
def test_document_get_is_read_only_when_callback_is_pending(client, scenario, endpoint, job_status):
    scenario.job.status = job_status
    scenario.job.save(update_fields=["status", "updated_at"])
    if job_status == "DONE":
        AIResultModel.objects.create(job=scenario.job, payload={
            "problems": [{"number": 2, "text": "Unapplied AI proposal"}],
        })
    before = _snapshot()

    response = _get(client, scenario, endpoint, _token(scenario))

    assert response.status_code == 200
    data = response.json()[0] if endpoint == "list" else response.json()
    assert data["status"] == "processing"
    assert data["problem_count"] == 1
    assert data["ai_job_id"] == scenario.job.job_id
    assert _snapshot() == before


@pytest.mark.parametrize("endpoint", ["list", "job"])
@pytest.mark.parametrize("token_state", ["missing", "invalid", "expired", "old_version"])
def test_known_authentication_failures_return_401_without_writes(client, scenario, endpoint, token_state):
    token = _token(scenario)
    if token_state == "missing":
        token = None
    elif token_state == "invalid":
        token = "not-a-jwt"
    elif token_state == "expired":
        token.set_exp(from_time=timezone.now() - timedelta(hours=1), lifetime=timedelta(seconds=1))
    else:
        token["token_version"] = scenario.user.token_version + 1
    before = _snapshot()

    response = _get(client, scenario, endpoint, token)

    assert response.status_code == 401
    assert response["WWW-Authenticate"].startswith("Bearer ")
    assert response.json() == {"detail": "Authentication required", "code": "auth_required"}
    assert _snapshot() == before


@pytest.mark.parametrize("endpoint", ["list", "job"])
def test_valid_token_with_foreign_tenant_header_is_rejected(client, scenario, endpoint):
    foreign = _foreign_document().tenant
    before = _snapshot()

    response = _get(client, scenario, endpoint, _token(scenario), tenant=foreign)

    assert response.status_code == 401
    assert response["WWW-Authenticate"].startswith("Bearer ")
    assert response.json()["code"] == "auth_required"
    assert "Foreign document" not in response.content.decode()
    assert _snapshot() == before


def test_valid_staff_can_only_read_documents_in_their_tenant(client, scenario):
    foreign_doc = _foreign_document()
    before = _snapshot()
    token = _token(scenario)

    listed = _get(client, scenario, "list", token)
    assert listed.status_code == 200
    assert [doc["id"] for doc in listed.json()] == [scenario.doc.id]
    scenario.doc = foreign_doc
    assert _get(client, scenario, "job", token).status_code == 404
    assert _snapshot() == before


@pytest.mark.parametrize("endpoint", ["list", "job"])
def test_document_reads_still_require_staff(scenario, endpoint):
    user = get_user_model().objects.create_user(username="qa-matchup-no-role", tenant=scenario.tenant)
    request = RequestFactory().get(_path(scenario, endpoint))
    request.tenant, request.user = scenario.tenant, user
    handler = views.DocumentListView().get if endpoint == "list" else (
        lambda request: views.DocumentJobView().get(request, scenario.doc.id)
    )
    before = _snapshot()

    response = SafeMethodDatabaseWriteMiddleware(handler)(request)

    assert response.status_code == 403
    assert json.loads(response.content) == {"detail": "Staff only"}
    assert _snapshot() == before
