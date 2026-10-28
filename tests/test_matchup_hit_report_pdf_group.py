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
def test_curated_pdf_groups_two_selected_problems_per_body_page(_safe_url):
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

    # 상담·공유 가독성 계약: 후보는 최대 2개씩 표시한다.
    assert _page_count(pdf_bytes) == 3  # cover + two grouped body pages (2 + 1)


@patch("apps.domains.matchup.pdf_report._safe_url", return_value="")
def test_curated_pdf_splits_five_selected_problems_two_per_page(_safe_url):
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

    assert _page_count(pdf_bytes) == 4  # cover + three body pages (2 + 2 + 1)


@pytest.mark.parametrize(
    "selected,comment,excluded,image_present,expected_comments,expected_selected,total",
    [
        (True, "", False, True, 0, 1, 3),
        (False, "  지도 메모  ", False, True, 1, 0, 3),
        (True, "지도 메모", False, True, 1, 1, 3),
        (True, " \n\t\u3000", False, True, 0, 1, 3),
        (True, "제외 문항 메모", True, True, 0, 0, 2),
        (False, "이미지 없는 문항 메모", False, False, 0, 0, 2),
        (False, "", False, True, 0, 0, 3),
    ],
    ids=["selection-only", "comment-only", "both", "whitespace", "excluded", "no-image", "summary-only"],
)
@patch("apps.domains.matchup.pdf_report._safe_url", return_value="")
def test_pdf_cover_counts_only_comments_on_included_questions(
    _safe_url, selected, comment, excluded, image_present,
    expected_comments, expected_selected, total,
):
    tenant = Tenant.objects.create(name="PDF Comments", code="pdf-comments")
    exam_doc = _document(tenant=tenant, name="exam-comments", problem_count=3)
    source_doc = _document(tenant=tenant, name="source-comments", problem_count=1)
    problems = [_problem(tenant=tenant, document=exam_doc, number=i) for i in range(1, 4)]
    candidate = _problem(tenant=tenant, document=source_doc, number=1)
    for problem in [problems[0], candidate]:
        problem.embedding = [1.0, 0.0]
        problem.meta = {"bbox": [0, 0, 1, 1]}
        problem.save(update_fields=["embedding", "meta"])
    if not image_present:
        problems[0].image_key = ""
        problems[0].save(update_fields=["image_key"])
    report = MatchupHitReport.objects.create(
        tenant=tenant, document=exam_doc, title="Comment count",
        summary="총평은 문항별 코멘트가 아닙니다.",
    )
    MatchupHitReportEntry.objects.create(
        tenant=tenant, report=report, exam_problem=problems[0],
        selected_problem_ids=[candidate.id] if selected else [],
        comment=comment, excluded=excluded,
    )
    before_entries = list(report.entries.values())
    before_report = MatchupHitReport.objects.values().get(pk=report.pk)

    pdf_bytes = generate_curated_hit_report_pdf(report)

    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        cover = pdf.pages[0].extract_text()
        assert len(pdf.pages) == 1 + total
    assert f"코멘트 작성: {expected_comments} / {total} 문항" in cover
    assert f"자료 선택: {expected_selected} / {total} 문항" in cover
    assert f"선택된 학원 자료 총 {expected_selected}건" in cover
    assert ("33.3%" if expected_selected else "큐레이션된 문항이 없습니다") in cover
    assert list(report.entries.values()) == before_entries
    assert MatchupHitReport.objects.values().get(pk=report.pk) == before_report
