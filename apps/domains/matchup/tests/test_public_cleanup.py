from __future__ import annotations

from io import BytesIO
from unittest.mock import patch
from uuid import uuid4

import pytest
from django.apps import apps
from PIL import Image, ImageDraw

from academy.adapters.ai.image_cleanup import (
    MarkCleanupResult,
    remove_colored_marks_from_image_bytes,
)
from apps.domains.matchup.models import MatchupDocument, MatchupProblem
from apps.domains.matchup.services import (
    clean_problem_public_image,
    get_problem_public_image_key,
    public_image_key_for_report,
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


def test_remove_colored_marks_reduces_red_pixels():
    source = _synthetic_problem_image_bytes()
    result = remove_colored_marks_from_image_bytes(source)

    assert result.mask_ratio > 0
    assert result.width == 220
    assert result.height == 120
    assert _red_pixel_count(result.image_bytes) < _red_pixel_count(source) * 0.25


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
    assert problem.meta["public_cleanup"]["red_mask_ratio"] == pytest.approx(0.031)
    upload.assert_called_once()
    assert upload.call_args.kwargs["key"] == public_key


def test_public_image_key_is_ignored_when_source_image_changes():
    problem = MatchupProblem(
        image_key="tenants/1/matchup/problems/new.png",
        meta={
            "public_cleanup": {
                "source_image_key": "tenants/1/matchup/problems/old.png",
                "public_image_key": "tenants/1/matchup/public-cleanup/problems/1.png",
            },
        },
    )

    assert get_problem_public_image_key(problem) == ""
    assert public_image_key_for_report(problem) == problem.image_key
