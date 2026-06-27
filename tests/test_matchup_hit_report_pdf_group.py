from __future__ import annotations

import io
from unittest.mock import patch

import pdfplumber
import pytest

from apps.core.models import Tenant
from apps.domains.inventory.models import InventoryFile
from apps.domains.matchup.models import (
    MatchupDocument,
    MatchupHitReport,
    MatchupHitReportEntry,
    MatchupProblem,
)
from apps.domains.matchup.pdf_report import generate_curated_hit_report_pdf

pytestmark = pytest.mark.django_db


def _inventory_file(*, tenant: Tenant, name: str) -> InventoryFile:
    return InventoryFile.objects.create(
        tenant=tenant,
        scope="admin",
        student_ps="",
        display_name=name,
        r2_key=f"tests/matchup/{name}.pdf",
        original_name=f"{name}.pdf",
        size_bytes=100,
        content_type="application/pdf",
    )


def _document(*, tenant: Tenant, name: str, problem_count: int = 0) -> MatchupDocument:
    return MatchupDocument.objects.create(
        tenant=tenant,
        inventory_file=_inventory_file(tenant=tenant, name=name),
        title=name,
        category="pdf-group",
        r2_key=f"tests/matchup/{name}/source.pdf",
        original_name=f"{name}.pdf",
        size_bytes=100,
        content_type="application/pdf",
        status="done",
        problem_count=problem_count,
    )


def _problem(*, tenant: Tenant, document: MatchupDocument, number: int) -> MatchupProblem:
    return MatchupProblem.objects.create(
        tenant=tenant,
        document=document,
        number=number,
        text=f"problem {number}",
        image_key=f"tests/matchup/{document.id}/{number}.png",
        meta={},
    )


def _page_count(pdf_bytes: bytes) -> int:
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        return len(pdf.pages)


@patch("apps.domains.matchup.pdf_report._safe_url", return_value="")
def test_curated_pdf_groups_multiple_selected_problems_on_one_body_page(_safe_url):
    tenant = Tenant.objects.create(name="PDF Group", code="pdf-group")
    exam_doc = _document(tenant=tenant, name="exam", problem_count=1)
    source_doc = _document(tenant=tenant, name="source", problem_count=3)
    exam_problem = _problem(tenant=tenant, document=exam_doc, number=1)
    selected = [
        _problem(tenant=tenant, document=source_doc, number=1),
        _problem(tenant=tenant, document=source_doc, number=2),
        _problem(tenant=tenant, document=source_doc, number=3),
    ]
    report = MatchupHitReport.objects.create(
        tenant=tenant,
        document=exam_doc,
        title="grouped",
    )
    MatchupHitReportEntry.objects.create(
        tenant=tenant,
        report=report,
        exam_problem=exam_problem,
        selected_problem_ids=[p.id for p in selected],
        comment="covered from several angles",
    )

    pdf_bytes = generate_curated_hit_report_pdf(report)

    assert _page_count(pdf_bytes) == 2  # cover + one grouped body page


@patch("apps.domains.matchup.pdf_report._safe_url", return_value="")
def test_curated_pdf_splits_more_than_four_selected_problems(_safe_url):
    tenant = Tenant.objects.create(name="PDF Split", code="pdf-split")
    exam_doc = _document(tenant=tenant, name="exam-split", problem_count=1)
    source_doc = _document(tenant=tenant, name="source-split", problem_count=5)
    exam_problem = _problem(tenant=tenant, document=exam_doc, number=1)
    selected = [
        _problem(tenant=tenant, document=source_doc, number=i)
        for i in range(1, 6)
    ]
    report = MatchupHitReport.objects.create(
        tenant=tenant,
        document=exam_doc,
        title="split",
    )
    MatchupHitReportEntry.objects.create(
        tenant=tenant,
        report=report,
        exam_problem=exam_problem,
        selected_problem_ids=[p.id for p in selected],
    )

    pdf_bytes = generate_curated_hit_report_pdf(report)

    assert _page_count(pdf_bytes) == 3  # cover + two body pages (4 + 1)
