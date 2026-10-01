import os
import shutil
import tempfile
import io
from uuid import uuid4

import fitz
import pytest
from PIL import Image

from apps.core.models import Tenant
from apps.domains.inventory.models import InventoryFile
from apps.domains.matchup import services
from apps.domains.matchup.models import MatchupDocument, MatchupProblem
from academy.adapters.tools.pymupdf_renderer import PdfDocument

pytestmark = pytest.mark.django_db


@pytest.fixture
def page_source(tmp_path, monkeypatch):
    from apps.infrastructure.storage import r2

    source = tmp_path / "photo-exam.pdf"
    with fitz.open() as pdf:
        for index in range(4):
            page = pdf.new_page(width=300, height=500)
            page.insert_text((30, 60), f"Question {index + 1}")
        pdf.save(source)
    downloads, renders, uploaded = [], [], []

    def download(inventory):
        fd, path = tempfile.mkstemp(dir=tmp_path, suffix=".pdf")
        os.close(fd)
        shutil.copyfile(source, path)
        downloads.append(path)
        return path

    render = PdfDocument.render_page

    def render_page(self, index, **kwargs):
        renders.append(index)
        return render(self, index, **kwargs)

    monkeypatch.setattr(services, "_download_inventory_to_temp", download)
    monkeypatch.setattr(PdfDocument, "render_page", render_page)
    monkeypatch.setattr(r2, "upload_fileobj_to_r2_storage", lambda **kwargs: uploaded.append(kwargs["fileobj"].read()))
    monkeypatch.setattr(r2, "generate_presigned_get_url_storage", lambda **kwargs: f"https://example.test/{kwargs['key']}")
    tenant = Tenant.objects.create(code="qa-manual-page", name="직접 자르기 검증")
    key = f"qa/{uuid4()}.pdf"
    inventory = InventoryFile.objects.create(
        tenant=tenant, scope="admin", display_name="사진 시험지", r2_key=key,
        original_name="photo.pdf", content_type="application/pdf",
    )
    doc = MatchupDocument.objects.create(
        tenant=tenant, inventory_file=inventory, title="사진 시험지", r2_key=key,
        original_name="photo.pdf", status="failed", problem_count=0,
    )
    return doc, downloads, renders, uploaded


def test_failed_ai_document_opens_one_page_and_reuses_cache(page_source):
    doc, downloads, renders, uploaded = page_source
    pages = services.ensure_document_page_images(doc, page_index=0)
    assert len(pages) == 4
    assert pages[0]["url"]
    assert all(not page["url"] for page in pages[1:])
    assert renders == [0]
    assert len(uploaded) == 1 and uploaded[0].startswith(b"\x89PNG")
    assert all(not os.path.exists(path) for path in downloads)
    doc.refresh_from_db()
    services.ensure_document_page_images(doc, page_index=0)
    assert len(downloads) == 1
    pages = services.ensure_document_page_images(doc, page_index=2)
    assert pages[0]["url"] and pages[2]["url"]
    assert renders == [0, 2]
    doc.refresh_from_db()
    assert doc.status == "failed" and doc.problem_count == 0
    assert "page_image_keys" not in doc.meta


def test_render_preserves_ai_result_arriving_during_preparation(page_source, monkeypatch):
    doc, _, _, _ = page_source
    render = services._render_and_upload_pages

    def concurrent_render(document, **kwargs):
        result = render(document, **kwargs)
        MatchupDocument.objects.filter(pk=doc.pk).update(meta={"ai_result": "arrived"})
        return result

    monkeypatch.setattr(services, "_render_and_upload_pages", concurrent_render)
    services.ensure_document_page_images(doc, page_index=1)
    doc.refresh_from_db()
    assert doc.meta["ai_result"] == "arrived"
    assert doc.meta["manual_page_image_keys"][1]


def test_failed_ai_document_manual_crop_persists_and_keeps_page_cache(page_source, monkeypatch):
    doc, downloads, renders, uploaded = page_source
    monkeypatch.setattr(services, "_enqueue_manual_problem_index", lambda problem: None)
    services.ensure_document_page_images(doc, page_index=2)
    problem = services.manually_crop_problem(
        doc, page_index=2, bbox_norm=(0.1, 0.1, 0.6, 0.3), number=1,
    )
    saved = MatchupProblem.objects.get(pk=problem.pk, tenant=doc.tenant)
    assert saved.document_id == doc.id and saved.number == 1
    assert saved.meta["page_index"] == 2
    assert saved.meta["bbox_norm"] == [0.1, 0.1, 0.6, 0.3]
    assert saved.image_key and len(uploaded) == 2
    assert uploaded[1].startswith(b"\x89PNG")
    assert renders == [2, 2]
    assert all(not os.path.exists(path) for path in downloads)
    doc.refresh_from_db()
    assert doc.problem_count == 1 and doc.status == "done"
    assert doc.meta["manual_page_image_keys"][2]


def test_out_of_range_page_does_not_cache_or_upload(page_source):
    doc, downloads, renders, uploaded = page_source
    with pytest.raises(ValueError):
        services.ensure_document_page_images(doc, page_index=4)
    assert not renders and not uploaded
    assert all(not os.path.exists(path) for path in downloads)
    doc.refresh_from_db()
    assert not doc.meta


def test_oversized_pdf_preview_crop_reload_and_existing_cache_are_safe(page_source, monkeypatch, tmp_path):
    doc, downloads, renders, uploaded = page_source
    source = tmp_path / "photo-exam.pdf"
    source.unlink()
    with fitz.open() as pdf:
        page = pdf.new_page(width=4284, height=5712)
        page.draw_rect(fitz.Rect(428.4, 571.2, 2570.4, 2284.8), color=None, fill=(1, 0, 0))
        pdf.new_page(width=4284, height=5712)
        pdf.save(source)
    original_meta = {
        "page_image_keys": ["ai-huge/0.png", "ai-huge/1.png"],
        "page_dimensions": [[8925, 11900], [8925, 11900]],
        "ai_result": "preserve",
    }
    doc.meta = original_meta
    doc.save(update_fields=["meta"])
    monkeypatch.setattr(services, "_enqueue_manual_problem_index", lambda problem: None)
    pages = services.ensure_document_page_images(doc, page_index=0)
    assert pages[0]["url"] and not pages[1]["url"]
    for width, height in [(pages[0]["width"], pages[0]["height"]), Image.open(io.BytesIO(uploaded[0])).size]:
        assert max(width, height) <= 3000 and width * height <= 8_000_000
    doc.refresh_from_db()
    assert doc.meta["page_image_keys"] == original_meta["page_image_keys"]
    assert doc.meta["page_dimensions"] == original_meta["page_dimensions"]
    assert doc.meta["ai_result"] == "preserve"
    assert "manual-bounded-v1" in doc.meta["manual_page_image_keys"][0]
    before = len(downloads)
    services.ensure_document_page_images(doc, page_index=0)
    assert len(downloads) == before
    problem = services.manually_crop_problem(
        doc, page_index=0, bbox_norm=(0.1, 0.1, 0.5, 0.3), number=1,
    )
    crop = Image.open(io.BytesIO(uploaded[-1]))
    assert crop.getpixel((crop.width // 2, crop.height // 2)) == (255, 0, 0)
    assert abs(crop.width / pages[0]["width"] - 0.5) < 0.002
    assert abs(crop.height / pages[0]["height"] - 0.3) < 0.002
    saved = MatchupProblem.objects.get(pk=problem.pk, tenant=doc.tenant)
    assert saved.meta["bbox_norm"] == [0.1, 0.1, 0.5, 0.3]
    doc.refresh_from_db()
    first_dimensions = doc.meta["manual_page_dimensions"][0]
    services.ensure_document_page_images(doc, page_index=1)
    doc.refresh_from_db()
    assert doc.meta["manual_page_dimensions"][0] == first_dimensions
    assert doc.meta["page_image_keys"] == original_meta["page_image_keys"]
    assert doc.problem_count == 1 and doc.status == "done"
    assert all(not os.path.exists(path) for path in downloads)


def test_download_timeout_removes_partial_file(tmp_path, monkeypatch):
    import urllib.request
    from types import SimpleNamespace
    from apps.infrastructure.storage import r2

    partial = tmp_path / "partial.pdf"
    original_mkstemp = tempfile.mkstemp
    paths = []

    def tracked_temp(**kwargs):
        fd, path = original_mkstemp(dir=tmp_path, **kwargs)
        paths.append(path)
        return fd, path

    def timeout(url, *, timeout):
        assert timeout == 30
        raise TimeoutError("fixture timeout")

    monkeypatch.setattr(tempfile, "mkstemp", tracked_temp)
    monkeypatch.setattr(urllib.request, "urlopen", timeout)
    monkeypatch.setattr(r2, "generate_presigned_get_url_storage", lambda **kwargs: "https://example.test/source.pdf")
    with pytest.raises(TimeoutError):
        services._download_inventory_to_temp(SimpleNamespace(r2_key=str(partial), original_name="source.pdf"))
    assert paths and all(not os.path.exists(path) for path in paths)
