from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.test import override_settings
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken

from apps.core.models import Tenant, TenantMembership
from apps.domains.ai.models import AIJobModel, AIResultModel
from apps.domains.tools.problem_review.views import _refresh_analysis
from apps.domains.tools.problem_studio.models import ProblemReviewReport
from tests.test_problem_review_report import _sample_report


pytestmark = pytest.mark.django_db


@pytest.fixture
def scenario():
    tenant = Tenant.objects.create(code="qa-review-read", name="Review QA")
    user = get_user_model().objects.create_user(username="qa-review-read", tenant=tenant, is_staff=True)
    TenantMembership.ensure_active(tenant=tenant, user=user, role="teacher")
    report = ProblemReviewReport.objects.create(
        tenant=tenant, requested_by=user, title="Analysis input",
        status=ProblemReviewReport.Status.ANALYZING, analysis_job_id="qa-review-read-job",
    )
    job = AIJobModel.objects.create(
        job_id=report.analysis_job_id, job_type="problem_review_analysis", status="DONE",
        tenant_id=str(tenant.id), tier="basic", source_domain="tools_problem_review",
        source_id=str(report.id), payload={"request_user_id": user.id},
    )
    AIResultModel.objects.create(job=job, payload={"report": _sample_report(), "source": {"question_count": 2}})
    token = AccessToken.for_user(user)
    token["tenant_id"], token["token_version"] = tenant.id, user.token_version
    client = APIClient(HTTP_HOST="api.hakwonplus.com", HTTP_X_TENANT_CODE=tenant.code)
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    with override_settings(
        ALLOWED_HOSTS=["api.hakwonplus.com", "testserver"],
        TENANT_HEADER_CODE_ALLOWED_HOSTS=("api.hakwonplus.com",),
        MIDDLEWARE=[
            "apps.core.middleware.safe_method_write.SafeMethodDatabaseWriteMiddleware",
            "apps.core.middleware.tenant.TenantMiddleware",
        ],
    ):
        yield SimpleNamespace(tenant=tenant, user=user, report=report, job=job, client=client)


def snapshot():
    return {model: list(model.objects.order_by("pk").values()) for model in (ProblemReviewReport, AIJobModel, AIResultModel)}


@pytest.mark.parametrize("endpoint", ["list", "detail"])
@pytest.mark.parametrize("status", ["DONE", "FAILED", "DEAD", "CANCELLED", "RUNNING"])
def test_analysis_status_is_readable_without_database_writes(scenario, endpoint, status):
    case = scenario
    case.job.status = status
    case.job.error_message = "Synthetic analysis failure" if status in ("FAILED", "DEAD", "CANCELLED") else ""
    case.job.save(update_fields=["status", "error_message"])
    before = snapshot()
    path = "/api/v1/tools/problem-review/reports/"
    if endpoint == "detail":
        path += f"{case.report.id}/"
    response = case.client.get(path)
    assert response.status_code == 200, response.content
    report = response.json()["reports"][0] if endpoint == "list" else response.json()
    assert report["status"] == ("draft" if status == "DONE" else "analyzing" if status == "RUNNING" else "failed")
    assert snapshot() == before


@pytest.mark.parametrize("string_version", [False, True])
def test_completed_analysis_read_edit_save_and_reload_preserves_teacher_work(scenario, string_version):
    case = scenario
    path = f"/api/v1/tools/problem-review/reports/{case.report.id}/"
    response = case.client.get(path)
    assert response.status_code == 200
    draft = deepcopy(response.json()["draft"])
    draft["summary"]["one_line"] = "Teacher's reviewed analysis"
    version = response.json()["version"]
    saved = case.client.patch(path, {"version": str(version) if string_version else version, "draft": draft}, format="json")
    assert saved.status_code == 200, saved.content
    assert saved.json()["version"] == 2
    case.report.refresh_from_db()
    assert case.report.status == "draft"
    assert case.report.draft["summary"]["one_line"] == "Teacher's reviewed analysis"
    before = snapshot()
    stale = case.client.patch(path, {"version": version, "draft": _sample_report()}, format="json")
    assert stale.status_code == 409
    assert snapshot() == before
    reloaded = case.client.get(path)
    assert reloaded.status_code == 200
    assert reloaded.json()["draft"]["summary"]["one_line"] == "Teacher's reviewed analysis"
    assert snapshot() == before


@pytest.mark.parametrize("status", ["DONE", "FAILED"])
def test_stale_analysis_object_does_not_overwrite_a_newer_manual_draft(scenario, status):
    case = scenario
    stale = ProblemReviewReport.objects.get(pk=case.report.pk)
    manual = _sample_report()
    manual["summary"]["one_line"] = "Already saved by the teacher"
    ProblemReviewReport.objects.filter(pk=case.report.pk).update(status="draft", draft=manual, version=2)
    case.job.status = status
    case.job.save(update_fields=["status"])
    before = snapshot()
    refreshed = _refresh_analysis(stale)
    assert refreshed.status == "draft"
    assert refreshed.version == 2
    assert refreshed.draft["summary"]["one_line"] == "Already saved by the teacher"
    assert snapshot() == before


@pytest.mark.parametrize("field,value", [("tenant_id", "999999"), ("source_id", "other-report"), ("source_domain", "other-domain"), ("payload", {"request_user_id": -1})])
def test_foreign_job_result_never_appears_in_report(scenario, field, value):
    case = scenario
    setattr(case.job, field, value)
    case.job.save(update_fields=[field])
    before = snapshot()
    response = case.client.get(f"/api/v1/tools/problem-review/reports/{case.report.id}/")
    assert response.status_code == 200
    assert response.json()["status"] == "analyzing"
    assert not response.json()["draft"]
    assert snapshot() == before


@pytest.mark.parametrize("action", ["patch", "finalize", "publish"])
@pytest.mark.parametrize("version", [True, False, 0, 1.9, "1.5", None, [], {}])
def test_invalid_versions_are_400_without_report_or_publication_mutation(scenario, action, version):
    case = scenario
    case.report.status = "draft"
    case.report.draft = _sample_report()
    case.report.save(update_fields=["status", "draft"])
    before = snapshot()
    path = f"/api/v1/tools/problem-review/reports/{case.report.id}/"
    method = case.client.patch if action == "patch" else case.client.post
    if action != "patch":
        path += {"finalize": "verification/", "publish": "publication/"}[action]
    with patch("apps.domains.tools.problem_review.views.render_problem_review_report") as render:
        response = method(path, {"version": version, "draft": _sample_report()}, format="json")
    assert response.status_code == 400, response.content
    assert snapshot() == before
    render.assert_not_called()
