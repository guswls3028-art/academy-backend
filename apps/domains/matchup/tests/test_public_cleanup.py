from __future__ import annotations

from io import BytesIO
from unittest.mock import patch
from uuid import uuid4

import pytest
from django.apps import apps
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image, ImageDraw

from academy.adapters.ai.image_cleanup import (
    MarkCleanupResult,
    remove_colored_marks_from_image_bytes,
)
from apps.domains.matchup.models import MatchupDocument, MatchupProblem
from apps.domains.matchup.services import (
    approve_problem_public_image,
    clean_document_public_images,
    clean_problem_public_image,
    dispatch_document_public_cleanup,
    get_problem_public_image_key,
    mark_document_public_cleanup_failed,
    public_image_key_for_report,
    upload_problem_public_image,
)


def _synthetic_problem_image_bytes() -> bytes:
    img = Image.new("RGB", (220, 120), "white")
    draw = ImageDraw.Draw(img)
    draw.rectangle((20, 35, 190, 78), outline="black", width=3)
    draw.line((28, 56, 180, 56), fill="black", width=2)
    draw.ellipse((145, 15, 205, 75), outline=(230, 30, 45), width=7)
    draw.line((155, 88, 205, 108), fill=(240, 40, 80), width=5)
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _red_pixel_count(image_bytes: bytes) -> int:
    img = Image.open(BytesIO(image_bytes)).convert("RGB")
    return sum(
        1
        for r, g, b in img.getdata()
        if r > 140 and r > g + 35 and r > b + 35
    )


def _dark_pixel_count(image_bytes: bytes, box: tuple[int, int, int, int] | None = None) -> int:
    img = Image.open(BytesIO(image_bytes)).convert("RGB")
    if box is not None:
        img = img.crop(box)
    return sum(1 for r, g, b in img.getdata() if max(r, g, b) < 90)


def test_remove_colored_marks_reduces_red_pixels():
    source = _synthetic_problem_image_bytes()
    result = remove_colored_marks_from_image_bytes(source)

    assert result.mask_ratio > 0
    assert result.width == 220
    assert result.height == 120
    assert _red_pixel_count(result.image_bytes) < _red_pixel_count(source) * 0.25
    assert result.version == "student-marks-v3"
    assert result.quality_status == "ready"


def test_remove_student_marks_reduces_thick_dark_handwriting():
    img = Image.new("RGB", (260, 160), "white")
    draw = ImageDraw.Draw(img)
    # Thin printed structure should survive the conservative dark-mark pass.
    draw.rectangle((25, 45, 235, 88), outline="black", width=1)
    draw.line((35, 68, 225, 68), fill="black", width=1)
    # Thick handwritten circle/check around the left side should be removed.
    draw.ellipse((18, 28, 92, 104), outline="black", width=8)
    draw.line((22, 112, 72, 142), fill="black", width=7)
    buf = BytesIO()
    img.save(buf, format="PNG")
    source = buf.getvalue()

    result = remove_colored_marks_from_image_bytes(source)

    assert result.dark_mask_ratio > 0
    assert result.quality_status == "review_required"
    assert "dark_marks_detected" in result.review_reasons
    assert _dark_pixel_count(result.image_bytes, (0, 20, 105, 150)) < (
        _dark_pixel_count(source, (0, 20, 105, 150)) * 0.55
    )
    assert _dark_pixel_count(result.image_bytes, (120, 40, 245, 95)) >= (
        _dark_pixel_count(source, (120, 40, 245, 95)) * 0.75
    )


@pytest.mark.django_db
def test_clean_problem_public_image_stores_public_cleanup_meta():
    Tenant = apps.get_model("core", "Tenant")
    InventoryFile = apps.get_model("inventory", "InventoryFile")

    suffix = uuid4().hex[:8]
    tenant = Tenant.objects.create(code=f"cleanup-{suffix}", name="cleanup")
    inventory = InventoryFile.objects.create(
        tenant=tenant,
        scope="admin",
        student_ps="",
        display_name="cleanup.pdf",
        r2_key=f"cleanup-{suffix}.pdf",
        original_name="cleanup.pdf",
        content_type="application/pdf",
        size_bytes=0,
    )
    document = MatchupDocument.objects.create(
        tenant=tenant,
        inventory_file=inventory,
        title="cleanup-doc",
        r2_key=inventory.r2_key,
        original_name=inventory.original_name,
        content_type=inventory.content_type,
        size_bytes=inventory.size_bytes,
    )
    problem = MatchupProblem.objects.create(
        tenant=tenant,
        document=document,
        number=1,
        text="cleanup",
        image_key=f"tenants/{tenant.id}/matchup/problems/source.png",
        meta={},
    )
    cleanup_result = MarkCleanupResult(
        image_bytes=b"cleaned",
        mask_ratio=0.031,
        mask_pixels=31,
        total_pixels=1000,
        width=100,
        height=10,
        red_mask_pixels=21,
        dark_mask_pixels=10,
        red_mask_ratio=0.021,
        dark_mask_ratio=0.01,
        quality_status="review_required",
        quality_score=0.2,
        review_reasons=("dark_marks_detected",),
    )

    with (
        patch(
            "apps.infrastructure.storage.r2.get_object_bytes_r2_storage",
            return_value=b"source",
        ),
        patch("apps.infrastructure.storage.r2.upload_fileobj_to_r2_storage") as upload,
        patch(
            "apps.domains.matchup.services.remove_colored_marks_from_image_bytes",
            return_value=cleanup_result,
        ),
    ):
        result = clean_problem_public_image(problem)

    problem.refresh_from_db()
    public_key = problem.meta["public_cleanup"]["public_image_key"]

    assert result["status"] == "processed"
    assert public_key == get_problem_public_image_key(problem)
    assert public_image_key_for_report(problem) == public_key
    assert problem.meta["public_cleanup"]["source_image_key"] == problem.image_key
    assert problem.meta["public_cleanup"]["mark_mask_ratio"] == pytest.approx(0.031)
    assert problem.meta["public_cleanup"]["red_mask_ratio"] == pytest.approx(0.021)
    assert problem.meta["public_cleanup"]["dark_mask_ratio"] == pytest.approx(0.01)
    assert problem.meta["public_cleanup"]["status"] == "review_required"
    assert problem.meta["public_cleanup"]["review_required"] is True
    assert problem.meta["public_cleanup"]["review_reasons"] == ["dark_marks_detected"]
    assert problem.meta["public_cleanup"]["version"] == "student-marks-v3"
    upload.assert_called_once()
    assert upload.call_args.kwargs["key"] == public_key


def test_public_image_key_is_ignored_when_source_image_changes():
    problem = MatchupProblem(
        image_key="tenants/1/matchup/problems/new.png",
        meta={
            "public_cleanup": {
                "source_image_key": "tenants/1/matchup/problems/old.png",
                "public_image_key": "tenants/1/matchup/public-cleanup/problems/1.png",
                "version": "student-marks-v3",
            },
        },
    )

    assert get_problem_public_image_key(problem) == ""
    assert public_image_key_for_report(problem) == problem.image_key


def test_public_image_key_is_ignored_when_cleanup_version_is_stale():
    problem = MatchupProblem(
        image_key="tenants/1/matchup/problems/source.png",
        meta={
            "public_cleanup": {
                "source_image_key": "tenants/1/matchup/problems/source.png",
                "public_image_key": "tenants/1/matchup/public-cleanup/problems/1-red-marks.png",
                "version": "red-marks-v1",
            },
        },
    )

    assert get_problem_public_image_key(problem) == ""
    assert public_image_key_for_report(problem) == problem.image_key


@pytest.mark.django_db
def test_clean_document_public_images_writes_official_readiness_summary():
    Tenant = apps.get_model("core", "Tenant")
    InventoryFile = apps.get_model("inventory", "InventoryFile")

    suffix = uuid4().hex[:8]
    tenant = Tenant.objects.create(code=f"cleanup-doc-{suffix}", name="cleanup")
    inventory = InventoryFile.objects.create(
        tenant=tenant,
        scope="admin",
        student_ps="",
        display_name="cleanup.pdf",
        r2_key=f"cleanup-doc-{suffix}.pdf",
        original_name="cleanup.pdf",
        content_type="application/pdf",
        size_bytes=0,
    )
    document = MatchupDocument.objects.create(
        tenant=tenant,
        inventory_file=inventory,
        title="cleanup-doc",
        r2_key=inventory.r2_key,
        original_name=inventory.original_name,
        content_type=inventory.content_type,
        size_bytes=inventory.size_bytes,
    )
    MatchupProblem.objects.create(
        tenant=tenant,
        document=document,
        number=1,
        text="cleanup",
        image_key=f"tenants/{tenant.id}/matchup/problems/source.png",
        meta={},
    )
    cleanup_result = MarkCleanupResult(
        image_bytes=b"cleaned",
        mask_ratio=0.031,
        mask_pixels=31,
        total_pixels=1000,
        width=100,
        height=10,
        red_mask_pixels=21,
        dark_mask_pixels=10,
        red_mask_ratio=0.021,
        dark_mask_ratio=0.01,
        quality_status="review_required",
        quality_score=0.2,
        review_reasons=("dark_marks_detected",),
    )

    with (
        patch("apps.infrastructure.storage.r2.get_object_bytes_r2_storage", return_value=b"source"),
        patch("apps.infrastructure.storage.r2.upload_fileobj_to_r2_storage"),
        patch(
            "apps.domains.matchup.services.remove_colored_marks_from_image_bytes",
            return_value=cleanup_result,
        ),
    ):
        result = clean_document_public_images(document, job_id="job-1")

    document.refresh_from_db()
    summary = document.meta["public_cleanup"]
    assert result["status"] == "review_required"
    assert result["official_ready"] is False
    assert result["review_required"] == 1
    assert summary["status"] == "review_required"
    assert summary["official_ready"] is False
    assert summary["job_id"] == "job-1"


@pytest.mark.django_db
def test_approve_problem_public_image_clears_review_required():
    problem = MatchupProblem(
        image_key="tenants/1/matchup/problems/source.png",
        meta={
            "public_cleanup": {
                "source_image_key": "tenants/1/matchup/problems/source.png",
                "public_image_key": "tenants/1/matchup/public-cleanup/problems/1-student_marks-v3.png",
                "version": "student-marks-v3",
                "status": "review_required",
                "review_required": True,
                "review_reasons": ["dark_marks_detected"],
            },
        },
    )
    problem.save = lambda *args, **kwargs: None

    result = approve_problem_public_image(problem)

    assert result["status"] == "approved"
    assert problem.meta["public_cleanup"]["status"] == "approved"
    assert problem.meta["public_cleanup"]["review_required"] is False
    assert problem.meta["public_cleanup"]["review_reasons"] == []


@pytest.mark.django_db
def test_upload_problem_public_image_stores_manual_public_copy():
    Tenant = apps.get_model("core", "Tenant")
    InventoryFile = apps.get_model("inventory", "InventoryFile")

    suffix = uuid4().hex[:8]
    tenant = Tenant.objects.create(code=f"manual-public-{suffix}", name="cleanup")
    inventory = InventoryFile.objects.create(
        tenant=tenant,
        scope="admin",
        student_ps="",
        display_name="cleanup.pdf",
        r2_key=f"manual-public-{suffix}.pdf",
        original_name="cleanup.pdf",
        content_type="application/pdf",
        size_bytes=0,
    )
    document = MatchupDocument.objects.create(
        tenant=tenant,
        inventory_file=inventory,
        title="cleanup-doc",
        r2_key=inventory.r2_key,
        original_name=inventory.original_name,
        content_type=inventory.content_type,
        size_bytes=inventory.size_bytes,
    )
    problem = MatchupProblem.objects.create(
        tenant=tenant,
        document=document,
        number=1,
        text="cleanup",
        image_key=f"tenants/{tenant.id}/matchup/problems/source.png",
        meta={},
    )
    img = Image.new("RGB", (40, 30), "white")
    buf = BytesIO()
    img.save(buf, format="PNG")
    upload_file = SimpleUploadedFile("clean.png", buf.getvalue(), content_type="image/png")

    with patch("apps.infrastructure.storage.r2.upload_fileobj_to_r2_storage") as upload:
        result = upload_problem_public_image(problem, image_file=upload_file)

    problem.refresh_from_db()
    cleanup = problem.meta["public_cleanup"]
    assert result["status"] == "approved"
    assert cleanup["mode"] == "manual_upload"
    assert cleanup["version"] == "manual-upload-v1"
    assert cleanup["status"] == "approved"
    assert get_problem_public_image_key(problem) == cleanup["public_image_key"]
    upload.assert_called_once()


@pytest.mark.django_db
def test_dispatch_document_public_cleanup_queues_worker_job():
    Tenant = apps.get_model("core", "Tenant")
    InventoryFile = apps.get_model("inventory", "InventoryFile")

    suffix = uuid4().hex[:8]
    tenant = Tenant.objects.create(code=f"dispatch-public-{suffix}", name="cleanup")
    inventory = InventoryFile.objects.create(
        tenant=tenant,
        scope="admin",
        student_ps="",
        display_name="cleanup.pdf",
        r2_key=f"dispatch-public-{suffix}.pdf",
        original_name="cleanup.pdf",
        content_type="application/pdf",
        size_bytes=0,
    )
    document = MatchupDocument.objects.create(
        tenant=tenant,
        inventory_file=inventory,
        title="cleanup-doc",
        r2_key=inventory.r2_key,
        original_name=inventory.original_name,
        content_type=inventory.content_type,
        size_bytes=inventory.size_bytes,
    )

    with patch(
        "apps.domains.ai.gateway.dispatch_job",
        return_value={"ok": True, "job_id": "job-1", "type": "matchup_public_cleanup"},
    ) as dispatch:
        result = dispatch_document_public_cleanup(document)

    document.refresh_from_db()
    assert result["ok"] is True
    assert document.meta["public_cleanup"]["status"] == "processing"
    assert document.meta["public_cleanup"]["job_id"] == "job-1"
    dispatch.assert_called_once()
    kwargs = dispatch.call_args.kwargs
    assert kwargs["job_type"] == "matchup_public_cleanup"
    assert kwargs["source_domain"] == "matchup_public_cleanup"
    assert kwargs["source_id"] == str(document.id)
    assert kwargs["tenant_id"] == str(tenant.id)


@pytest.mark.django_db
def test_mark_document_public_cleanup_failed_updates_document_summary():
    Tenant = apps.get_model("core", "Tenant")
    InventoryFile = apps.get_model("inventory", "InventoryFile")

    suffix = uuid4().hex[:8]
    tenant = Tenant.objects.create(code=f"callback-public-{suffix}", name="cleanup")
    inventory = InventoryFile.objects.create(
        tenant=tenant,
        scope="admin",
        student_ps="",
        display_name="cleanup.pdf",
        r2_key=f"callback-public-{suffix}.pdf",
        original_name="cleanup.pdf",
        content_type="application/pdf",
        size_bytes=0,
    )
    document = MatchupDocument.objects.create(
        tenant=tenant,
        inventory_file=inventory,
        title="cleanup-doc",
        r2_key=inventory.r2_key,
        original_name=inventory.original_name,
        content_type=inventory.content_type,
        size_bytes=inventory.size_bytes,
        meta={
            "public_cleanup": {
                "version": "student-marks-v3",
                "status": "processing",
                "official_ready": False,
                "job_id": "job-failed",
            },
        },
    )

    cleanup = mark_document_public_cleanup_failed(
        document,
        job_id="job-failed",
        error="worker crashed",
    )

    document.refresh_from_db()
    assert cleanup["status"] == "failed"
    assert cleanup["official_ready"] is False
    assert cleanup["job_id"] == "job-failed"
    assert cleanup["error"] == "worker crashed"
