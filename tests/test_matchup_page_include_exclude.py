"""excluded_pages rollback (include) + exclude regression tests.

These tests used to mock MatchupDocument directly. Page state is now persisted
through MatchupPageState, so the regression must exercise real model rows.
"""
from __future__ import annotations

from uuid import uuid4
from unittest.mock import patch

import pytest

from apps.core.models import Tenant
from apps.domains.inventory.models import InventoryFile, InventoryFolder
from apps.domains.matchup.models import MatchupDocument, MatchupPageState, MatchupProblem
from apps.domains.matchup.services import (
    PAGE_STATE_AUTO,
    PAGE_STATE_SKIP,
    exclude_page_from_matchup,
    include_page_to_matchup,
)


pytestmark = pytest.mark.django_db


@pytest.fixture
def tenant():
    suffix = uuid4().hex[:8]
    return Tenant.objects.create(code=f"include-exclude-{suffix}", name="include-exclude")


def _make_doc(tenant, meta=None):
    suffix = uuid4().hex[:8]
    folder = InventoryFolder.objects.create(
        tenant=tenant,
        scope="admin",
        student_ps="",
        parent=None,
        name=f"root-{suffix}",
    )
    inv = InventoryFile.objects.create(
        tenant=tenant,
        folder=folder,
        scope="admin",
        student_ps="",
        original_name=f"include-exclude-{suffix}.pdf",
        r2_key=f"tenants/{tenant.id}/include-exclude/{suffix}.pdf",
        size_bytes=100,
        content_type="application/pdf",
    )
    return MatchupDocument.objects.create(
        tenant=tenant,
        inventory_file=inv,
        title=f"include-exclude-{suffix}",
        r2_key=inv.r2_key,
        original_name=inv.original_name,
        size_bytes=inv.size_bytes,
        content_type=inv.content_type,
        status="done",
        meta={"page_count": 10, **(meta or {})},
    )


def _make_problem(document, *, number, page_index, manual=False, pinned=False):
    return MatchupProblem.objects.create(
        tenant=document.tenant,
        document=document,
        number=number,
        text=f"q{number}",
        meta={
            "page_index": page_index,
            **({"manual": True} if manual else {}),
            **({"manual_owner_pinned": True} if pinned else {}),
        },
    )


def test_include_page_removes_from_excluded_list(tenant):
    """Already-excluded page include removes meta flag and asks for reanalysis."""
    doc = _make_doc(tenant, meta={"excluded_pages": [1, 3, 5]})

    result = include_page_to_matchup(doc, 3)

    doc.refresh_from_db()
    assert result["excluded_pages"] == [1, 5]
    assert result["requires_reanalyze"] is True
    assert doc.meta["excluded_pages"] == [1, 5]
    assert MatchupPageState.objects.get(document=doc, page_index=3).state == PAGE_STATE_AUTO


def test_include_page_not_excluded_no_reanalysis(tenant):
    """Including a page that was already active is harmless but records state."""
    doc = _make_doc(tenant, meta={"excluded_pages": [1, 3]})

    result = include_page_to_matchup(doc, 7)

    doc.refresh_from_db()
    assert result["excluded_pages"] == [1, 3]
    assert result["requires_reanalyze"] is False
    assert doc.meta["excluded_pages"] == [1, 3]
    assert MatchupPageState.objects.get(document=doc, page_index=7).state == PAGE_STATE_AUTO


def test_include_page_empty_excluded_list_no_reanalysis(tenant):
    """Empty excluded_pages remains empty and does not request reanalysis."""
    doc = _make_doc(tenant, meta={})

    result = include_page_to_matchup(doc, 0)

    doc.refresh_from_db()
    assert result["excluded_pages"] == []
    assert result["requires_reanalyze"] is False
    assert doc.meta["excluded_pages"] == []


def test_include_page_invalid_index_raises(tenant):
    """page_index range validation is shared with exclude."""
    doc = _make_doc(tenant, meta={"excluded_pages": [0]})

    with pytest.raises(ValueError, match="page_index"):
        include_page_to_matchup(doc, -1)
    with pytest.raises(ValueError, match="page_index"):
        include_page_to_matchup(doc, 1000)


def test_include_page_meta_preserved(tenant):
    """include preserves unrelated document meta."""
    doc = _make_doc(
        tenant,
        meta={
            "excluded_pages": [2, 4],
            "source_type": "academy_workbook",
            "page_image_keys": ["key0", "key1"],
        },
    )

    include_page_to_matchup(doc, 2)

    doc.refresh_from_db()
    assert doc.meta["source_type"] == "academy_workbook"
    assert doc.meta["page_image_keys"] == ["key0", "key1"]
    assert doc.meta["excluded_pages"] == [4]


def test_include_page_normalizes_int_page_index(tenant):
    """int page_index is handled through the page-state SSOT."""
    doc = _make_doc(tenant, meta={"excluded_pages": [1, 2, 3]})

    result = include_page_to_matchup(doc, 2)

    assert 2 not in result["excluded_pages"]
    assert MatchupPageState.objects.get(document=doc, page_index=2).state == PAGE_STATE_AUTO


def test_exclude_then_include_round_trip(tenant):
    """exclude then include returns the page to auto state."""
    doc = _make_doc(tenant, meta={})

    exclude_result = exclude_page_from_matchup(doc, 5)
    assert exclude_result["excluded_pages"] == [5]

    include_result = include_page_to_matchup(doc, 5)

    doc.refresh_from_db()
    assert include_result["excluded_pages"] == []
    assert include_result["requires_reanalyze"] is True
    assert doc.meta["excluded_pages"] == []
    assert MatchupPageState.objects.get(document=doc, page_index=5).state == PAGE_STATE_AUTO


def test_exclude_page_preserves_manual_owner_pinned(tenant):
    """manual_owner_pinned=True auto problem is preserved on page exclude."""
    doc = _make_doc(tenant, meta={})
    pinned = _make_problem(doc, number=1, page_index=2, pinned=True)
    auto = _make_problem(doc, number=2, page_index=2)
    other_page = _make_problem(doc, number=3, page_index=5)

    with patch("apps.domains.matchup.services.delete_problem_with_r2") as mock_del:
        result = exclude_page_from_matchup(doc, 2)

    assert result["removed_problems"] == 1
    assert result["preserved_pinned"] == 1
    assert result["preserved_manual"] == 0
    mock_del.assert_called_once_with(auto)
    assert MatchupProblem.objects.filter(pk=pinned.pk).exists()
    assert MatchupProblem.objects.filter(pk=other_page.pk).exists()
    assert MatchupPageState.objects.get(document=doc, page_index=2).state == PAGE_STATE_SKIP


def test_exclude_page_preserves_manual_and_pinned_both(tenant):
    """manual=True and manual_owner_pinned=True are both protected."""
    doc = _make_doc(tenant, meta={})
    manual = _make_problem(doc, number=1, page_index=2, manual=True)
    pinned = _make_problem(doc, number=2, page_index=2, pinned=True)
    both = _make_problem(doc, number=3, page_index=2, manual=True, pinned=True)
    auto = _make_problem(doc, number=4, page_index=2)

    with patch("apps.domains.matchup.services.delete_problem_with_r2") as mock_del:
        result = exclude_page_from_matchup(doc, 2)

    assert result["removed_problems"] == 1
    assert result["preserved_manual"] == 2
    assert result["preserved_pinned"] == 1
    mock_del.assert_called_once_with(auto)
    assert MatchupProblem.objects.filter(pk=manual.pk).exists()
    assert MatchupProblem.objects.filter(pk=pinned.pk).exists()
    assert MatchupProblem.objects.filter(pk=both.pk).exists()
