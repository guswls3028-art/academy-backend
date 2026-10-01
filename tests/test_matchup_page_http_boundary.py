import os
import io
import json
import shutil
import tempfile
from datetime import timedelta
from types import SimpleNamespace

import fitz
import pytest
from django.contrib.auth import get_user_model
from django.test import Client, override_settings
from django.utils import timezone
from PIL import Image
from rest_framework_simplejwt.tokens import AccessToken

from apps.core.models import Tenant, TenantMembership
from apps.domains.ai.models import AIJobModel, AIResultModel
from apps.domains.inventory.models import InventoryFile
from apps.domains.matchup import services, views
from apps.domains.matchup.models import (
    MatchupArtifactScanIntent, MatchupDocument, MatchupHitReport,
    MatchupHitReportEntry, MatchupPageState, MatchupProblem,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def page_http(tmp_path, monkeypatch):
    from apps.infrastructure.storage import r2

    source = tmp_path / "exam.pdf"
    with fitz.open() as pdf:
        for index in range(3):
            page = pdf.new_page(width=300, height=500)
            page.insert_text((30, 60), f"Question {index + 1}")
        pdf.save(source)
    tenant = Tenant.objects.create(code="qa-page-http", name="Page HTTP boundary")
    user = get_user_model().objects.create_user(username="qa-page-http", tenant=tenant, is_staff=True)
    TenantMembership.objects.create(tenant=tenant, user=user, role="teacher", is_active=True)
    inventory = InventoryFile.objects.create(
        tenant=tenant, scope="admin", display_name="Exam", original_name="exam.pdf",
        r2_key=f"tenants/{tenant.id}/exam.pdf", content_type="application/pdf",
    )
    doc = MatchupDocument.objects.create(
        tenant=tenant, author=user, inventory_file=inventory, title="Exam",
        r2_key=inventory.r2_key, original_name=inventory.original_name,
        content_type=inventory.content_type, status="failed", problem_count=0,
    )
    downloads, uploads, upload_calls = [], {}, []

    def download(_inventory):
        fd, path = tempfile.mkstemp(dir=tmp_path, suffix=".pdf")
        os.close(fd)
        shutil.copyfile(source, path)
        downloads.append(path)
        return path

    def upload(**kwargs):
        upload_calls.append(kwargs["key"])
        uploads[kwargs["key"]] = kwargs["fileobj"].read()

    def presign(**kwargs):
        return f"https://example.test/{kwargs['key']}"

    monkeypatch.setattr(services, "_download_inventory_to_temp", download)
    monkeypatch.setattr(r2, "upload_fileobj_to_r2_storage", upload)
    monkeypatch.setattr(r2, "generate_presigned_get_url_storage", presign)
    monkeypatch.setattr(views, "generate_presigned_get_url_storage", presign)
    monkeypatch.setattr(services, "_enqueue_manual_problem_index", lambda problem: None)
    token = AccessToken.for_user(user)
    token["tenant_id"], token["token_version"] = tenant.id, user.token_version
    headers = {
        "HTTP_HOST": "api.hakwonplus.com", "HTTP_X_TENANT_CODE": tenant.code,
        "HTTP_AUTHORIZATION": f"Bearer {token}",
    }
    with override_settings(MIDDLEWARE=[
        "apps.core.middleware.safe_method_write.SafeMethodDatabaseWriteMiddleware",
        "apps.core.middleware.tenant.TenantMiddleware",
    ]):
        yield SimpleNamespace(
            client=Client(raise_request_exception=False), doc=doc, user=user,
            headers=headers, source=source, downloads=downloads, uploads=uploads, upload_calls=upload_calls,
        )


def _rows():
    return {
        model: list(model.objects.order_by("pk").values())
        for model in (
            MatchupDocument, InventoryFile, MatchupProblem, MatchupHitReport,
            MatchupHitReportEntry, MatchupPageState, MatchupArtifactScanIntent,
            AIJobModel, AIResultModel, Tenant, TenantMembership, get_user_model(),
        )
    }


@pytest.mark.parametrize("status", ["failed", "done"])
def test_cold_pages_get_is_read_only_and_requires_explicit_preparation(page_http, status):
    page_http.doc.status = status
    page_http.doc.save(update_fields=["status", "updated_at"])
    before = _rows()

    response = page_http.client.get(
        f"/api/v1/matchup/documents/{page_http.doc.id}/pages/?page_index=0",
        **page_http.headers,
    )

    assert response.status_code == 409, (response.status_code, len(page_http.uploads))
    assert response.json()["code"] == "pages_not_prepared"
    assert _rows() == before
    assert not page_http.uploads and not page_http.downloads


def _prepare(case, index=0):
    return case.client.post(
        f"/api/v1/matchup/documents/{case.doc.id}/pages/",
        data=json.dumps({"page_index": index}), content_type="application/json", **case.headers,
    )


def _read(case, index=0):
    before, uploads, downloads = _rows(), list(case.upload_calls), list(case.downloads)
    response = case.client.get(
        f"/api/v1/matchup/documents/{case.doc.id}/pages/",
        {"page_index": index} if index is not None else {}, **case.headers,
    )
    assert _rows() == before
    assert case.upload_calls == uploads and case.downloads == downloads
    return response


@pytest.mark.parametrize("status", ["failed", "done"])
def test_prepare_select_crop_and_reopen_persist_real_http(page_http, status):
    case = page_http
    case.doc.status = status
    case.doc.save(update_fields=["status", "updated_at"])
    assert _prepare(case).status_code == 200
    assert _read(case).status_code == 200
    assert len(case.uploads) == 1
    assert _read(case, 2).status_code == 409
    assert _prepare(case, 2).status_code == 200
    pages = _read(case, 2)
    assert pages.status_code == 200 and pages.json()["page_count"] == 3
    assert pages.json()["pages"][0]["url"] and pages.json()["pages"][2]["url"]
    saved = case.client.post(
        f"/api/v1/matchup/documents/{case.doc.id}/manual-crop/",
        data=json.dumps({"page_index": 2, "bbox": [0.1, 0.1, 0.5, 0.3], "number": 7, "text": "User text"}),
        content_type="application/json", **case.headers,
    )
    assert saved.status_code == 201
    problem = MatchupProblem.objects.get(pk=saved.json()["id"])
    assert problem.number == 7 and problem.text == "User text"
    assert problem.meta["bbox_norm"] == [0.1, 0.1, 0.5, 0.3]
    reopened = case.client.get(
        "/api/v1/matchup/problems/", {"document_id": case.doc.id}, **case.headers,
    )
    assert reopened.status_code == 200
    assert reopened.json()[0]["id"] == problem.id
    assert _read(case, 2).status_code == 200
    case.doc.refresh_from_db()
    assert case.doc.problem_count == 1 and case.doc.status == "done"
    assert len(case.uploads) == 3
    assert all(not os.path.exists(path) for path in case.downloads)
    assert _prepare(case, 2).status_code == 200
    assert len(case.uploads) == 3  # Warm prepare is idempotent.
    assert len(case.upload_calls) == 3


def test_oversized_ai_cache_is_preserved_and_manual_http_is_bounded(page_http):
    case = page_http
    case.source.unlink()
    with fitz.open() as pdf:
        pdf.new_page(width=4284, height=5712)
        pdf.new_page(width=4284, height=5712)
        pdf.save(case.source)
    original = {
        "page_image_keys": ["ai/0.png", "ai/1.png"],
        "page_dimensions": [[8925, 11900], [8925, 11900]], "ai_result": "keep",
    }
    case.doc.meta = original
    case.doc.save(update_fields=["meta"])
    assert _read(case).status_code == 409
    assert _prepare(case).status_code == 200
    selected = _read(case)
    assert selected.status_code == 200 and selected.json()["pages"][1]["url"] == ""
    image = Image.open(io.BytesIO(next(iter(case.uploads.values()))))
    assert max(image.size) <= 3000 and image.width * image.height <= 8_000_000
    case.doc.refresh_from_db()
    for field, value in original.items():
        assert case.doc.meta[field] == value
    assert _prepare(case, None).status_code == 200
    assert all(page["url"] for page in _read(case, None).json()["pages"])
    uploads = list(case.upload_calls)
    assert _prepare(case, None).status_code == 200
    assert case.upload_calls == uploads


@pytest.mark.parametrize("index", [0, None])
def test_http_prepare_merges_concurrent_ai_metadata(page_http, monkeypatch, index):
    case = page_http
    render = services._render_and_upload_pages

    def concurrent_render(document, **kwargs):
        result = render(document, **kwargs)
        MatchupDocument.objects.filter(pk=document.pk).update(meta={
            "ai_result": "arrived", "page_image_keys": ["ai/0.png"], "page_dimensions": [[625, 1042]],
        })
        return result

    monkeypatch.setattr(services, "_render_and_upload_pages", concurrent_render)
    assert _prepare(case, index).status_code == 200
    case.doc.refresh_from_db()
    assert case.doc.meta["ai_result"] == "arrived"
    assert case.doc.meta["page_image_keys"] == ["ai/0.png"]
    assert case.doc.meta["manual_page_image_keys"][0]
    assert _read(case, index).status_code == 200


def test_selected_prepare_preserves_another_manual_page_arriving_concurrently(page_http, monkeypatch):
    case = page_http
    render = services._render_and_upload_pages

    def concurrent_render(document, **kwargs):
        result = render(document, **kwargs)
        MatchupDocument.objects.filter(pk=document.pk).update(meta={
            "ai_result": "keep", "manual_page_image_keys": ["", "", "manual/2.png"],
            "manual_page_dimensions": [[625, 1042], [625, 1042], [625, 1042]],
        })
        return result

    monkeypatch.setattr(services, "_render_and_upload_pages", concurrent_render)
    assert _prepare(case, 1).status_code == 200
    case.doc.refresh_from_db()
    assert case.doc.meta["ai_result"] == "keep"
    assert case.doc.meta["manual_page_image_keys"][2] == "manual/2.png"
    assert _read(case, 1).status_code == 200 and _read(case, 2).status_code == 200


@pytest.mark.parametrize("size", [(800, 1200), (3200, 3200)])
def test_image_documents_also_prepare_and_reopen_with_safe_dimensions(page_http, size):
    case = page_http
    case.source.unlink()
    Image.new("RGB", size, "white").save(case.source, "PNG")
    inventory = case.doc.inventory_file
    inventory.original_name, inventory.content_type = "photo.png", "image/png"
    inventory.save(update_fields=["original_name", "content_type"])
    case.doc.original_name, case.doc.content_type = "photo.png", "image/png"
    case.doc.save(update_fields=["original_name", "content_type"])
    assert _prepare(case).status_code == 200
    response = _read(case)
    assert response.status_code == 200 and response.json()["is_pdf"] is False
    page = response.json()["pages"][0]
    assert page["url"] and max(page["width"], page["height"]) <= 3000
    assert page["width"] * page["height"] <= 8_000_000


def test_whole_page_prepare_keeps_existing_consumers_working(page_http):
    assert _prepare(page_http, None).status_code == 200
    result = _read(page_http, None)
    assert result.status_code == 200
    assert len(result.json()["pages"]) == 3 and all(page["url"] for page in result.json()["pages"])
    uploads = list(page_http.upload_calls)
    assert _prepare(page_http, None).status_code == 200
    assert page_http.upload_calls == uploads


def test_whole_post_after_individual_manual_pages_is_fully_read_only(page_http):
    case = page_http
    for index in range(3):
        assert _prepare(case, index).status_code == 200
    case.doc.refresh_from_db()
    assert not case.doc.meta.get("page_image_keys")
    assert all(case.doc.meta["manual_page_image_keys"])
    before, uploads, downloads = _rows(), list(case.upload_calls), list(case.downloads)
    for _ in range(2):
        response = _prepare(case, None)
        assert response.status_code == 200
        assert all(page["url"] for page in response.json()["pages"])
        assert _rows() == before
        assert case.upload_calls == uploads and case.downloads == downloads
        assert _read(case, None).status_code == 200


def test_prepare_failure_is_visible_and_can_be_retried(page_http, monkeypatch):
    from apps.infrastructure.storage import r2

    original = r2.upload_fileobj_to_r2_storage
    monkeypatch.setattr(r2, "upload_fileobj_to_r2_storage", lambda **kwargs: (_ for _ in ()).throw(RuntimeError("fixture storage failure")))
    before = _rows()
    assert _prepare(page_http).status_code == 500
    assert _rows() == before
    assert _read(page_http).status_code == 409
    monkeypatch.setattr(r2, "upload_fileobj_to_r2_storage", original)
    assert _prepare(page_http).status_code == 200
    assert _read(page_http).status_code == 200
    assert all(not os.path.exists(path) for path in page_http.downloads)


@pytest.mark.parametrize("index", [-1, 3, "invalid"])
def test_invalid_prepare_does_not_write_or_upload(page_http, index):
    before = _rows()
    assert _prepare(page_http, index).status_code == 400
    assert _rows() == before and not page_http.uploads


@pytest.mark.parametrize("method", ["get", "post"])
@pytest.mark.parametrize("boundary", ["missing", "invalid", "expired", "foreign", "role"])
def test_page_auth_tenant_role_boundaries_are_unchanged(page_http, method, boundary):
    case = page_http
    headers = dict(case.headers)
    expected = 401
    if boundary == "missing":
        headers.pop("HTTP_AUTHORIZATION")
    elif boundary == "invalid":
        headers["HTTP_AUTHORIZATION"] = "Bearer invalid"
    elif boundary == "expired":
        token = AccessToken.for_user(case.user)
        token["tenant_id"], token["token_version"] = case.doc.tenant_id, case.user.token_version
        token.set_exp(from_time=timezone.now() - timedelta(hours=1), lifetime=timedelta(seconds=1))
        headers["HTTP_AUTHORIZATION"] = f"Bearer {token}"
    elif boundary == "foreign":
        tenant = Tenant.objects.create(code="qa-page-foreign", name="Foreign")
        headers["HTTP_X_TENANT_CODE"] = tenant.code
    else:
        # This valid core role is not allowed by Matchup's existing staff check.
        case.user.is_staff = False
        case.user.save(update_fields=["is_staff"])
        TenantMembership.objects.filter(user=case.user).update(role="staff")
        expected = 403
    before = _rows()
    response = getattr(case.client, method)(
        f"/api/v1/matchup/documents/{case.doc.id}/pages/", **headers,
    )
    assert response.status_code == expected
    assert _rows() == before and not case.uploads and not case.downloads
