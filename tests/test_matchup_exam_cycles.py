import json
from uuid import uuid4

import pytest
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import RequestFactory

from apps.core.models import Tenant, TenantMembership
from apps.domains.inventory.models import InventoryFile
from apps.domains.matchup import services, views
from apps.domains.matchup.models import MatchupDocument, MatchupHitReport
from apps.domains.matchup.views_hit_report import HitReportLandingPublicView

pytestmark = pytest.mark.django_db
CYCLES = ["semester1_midterm", "semester1_final", "semester2_midterm", "semester2_final"]


@pytest.fixture
def staff():
    tenant = Tenant.objects.create(code="qa-exam-cycle", name="시험 회차 검증")
    user = get_user_model().objects.create_user(username="qa-cycle-staff", tenant=tenant, is_staff=True)
    TenantMembership.ensure_active(tenant=tenant, user=user, role="teacher")
    return tenant, user


@pytest.fixture
def storage(monkeypatch):
    from apps.infrastructure.storage import r2

    monkeypatch.setattr(views, "upload_fileobj_to_r2_storage", lambda **kwargs: None)
    monkeypatch.setattr(r2, "generate_presigned_get_url_storage", lambda **kwargs: "https://example.test/source.pdf")
    monkeypatch.setattr(services, "dispatch_ai_job", lambda **kwargs: {"ok": True, "job_id": str(uuid4())})


def request_for(staff, method, path, data):
    request = getattr(RequestFactory(), method)(path, data=data)
    request.tenant, request.user = staff
    return request


def make_document(tenant, user, cycle="midterm"):
    key = f"qa/{uuid4()}.pdf"
    inventory = InventoryFile.objects.create(
        tenant=tenant, scope="admin", display_name="시험지", r2_key=key,
        original_name="exam.pdf", content_type="application/pdf",
    )
    return MatchupDocument.objects.create(
        tenant=tenant, author=user, inventory_file=inventory, title="시험지",
        r2_key=key, original_name="exam.pdf", exam_cycle=cycle, exam_year=2026,
    )


@pytest.mark.parametrize("cycle", CYCLES)
def test_upload_saves_cycle_and_year_before_success(staff, storage, cycle):
    request = request_for(staff, "post", "/", {
        "file": SimpleUploadedFile("exam.pdf", b"%PDF-1.4", content_type="application/pdf"),
        "category": "학교", "exam_cycle": cycle, "exam_year": "2026",
    })
    response = views.DocumentUploadView().post(request)
    assert response.status_code == 201
    saved = MatchupDocument.objects.get(pk=json.loads(response.content)["id"])
    assert (saved.exam_cycle, saved.exam_year) == (cycle, 2026)
    listed = json.loads(views.DocumentListView().get(request).content)
    assert listed[0]["exam_cycle"] == cycle
    assert listed[0]["exam_year"] == 2026


def test_invalid_upload_does_not_create_storage_or_document(staff, storage, monkeypatch):
    uploads = []
    monkeypatch.setattr(views, "upload_fileobj_to_r2_storage", lambda **kwargs: uploads.append(kwargs))
    request = request_for(staff, "post", "/", {
        "file": SimpleUploadedFile("exam.pdf", b"%PDF", content_type="application/pdf"),
        "exam_cycle": "semester3_midterm",
    })
    response = views.DocumentUploadView().post(request)
    assert response.status_code == 400
    assert "exam_cycle" in json.loads(response.content)
    assert not uploads
    assert not InventoryFile.objects.exists()
    assert not MatchupDocument.objects.exists()


def test_patch_preserves_legacy_and_rejects_other_tenant(staff):
    tenant, user = staff
    doc = make_document(tenant, user)
    other = make_document(tenant, user, "final")
    request = RequestFactory().patch("/", data=json.dumps({"exam_cycle": CYCLES[2]}), content_type="application/json")
    request.tenant, request.user = staff
    assert views.DocumentDetailView().patch(request, doc.id).status_code == 200
    doc.refresh_from_db()
    other.refresh_from_db()
    assert doc.exam_cycle == CYCLES[2]
    assert other.exam_cycle == "final"
    request.tenant = Tenant.objects.create(code="qa-cycle-other", name="다른 학교")
    request.user = get_user_model().objects.create_user(username="qa-other-staff", tenant=request.tenant, is_staff=True)
    TenantMembership.ensure_active(tenant=request.tenant, user=request.user, role="teacher")
    assert views.DocumentDetailView().patch(request, doc.id).status_code == 404
    doc.refresh_from_db()
    assert doc.exam_cycle == CYCLES[2]


def test_public_cards_return_saved_classification_with_tenant_isolation(staff):
    tenant, user = staff
    doc = make_document(tenant, user, CYCLES[3])
    report = MatchupHitReport.objects.create(tenant=tenant, author=user, document=doc)
    foreign = Tenant.objects.create(code="qa-cycle-foreign", name="다른 학원")
    foreign_doc = make_document(foreign, None, CYCLES[0])
    foreign_report = MatchupHitReport.objects.create(tenant=foreign, document=foreign_doc)
    request = RequestFactory().get("/", {"ids": f"{report.id},{foreign_report.id}"})
    request.tenant = tenant
    cards = json.loads(HitReportLandingPublicView().get(request).content)["reports"]
    assert len(cards) == 1
    assert (cards[0]["exam_cycle"], cards[0]["exam_year"]) == (CYCLES[3], 2026)
