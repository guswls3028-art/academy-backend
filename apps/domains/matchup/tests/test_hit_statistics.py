import math
from types import SimpleNamespace
from uuid import uuid4

import pytest
from django.apps import apps

from apps.domains.matchup.models import (
    MatchupDocument,
    MatchupHitReport,
    MatchupHitReportEntry,
    MatchupProblem,
)
from apps.domains.matchup.pdf_report import (
    _calculate_hit_statistics,
    _compute_display_sim,
    calculate_matchup_hit_statistics,
)


@pytest.mark.parametrize(
    "text_pair,image_pair,bbox,expected",
    [
        (([1.0, 0.0], [0.5, math.sqrt(0.75)]), (None, None), True, 0.5),
        (([1.0, 0.0], [0.5, math.sqrt(0.75)]), ([1.0, 0.0], [0.5, math.sqrt(0.75)]), True, 0.5),
        (([1.0, 0.0], [1.0, 0.0]), (None, None), True, 1.0),
        (([1.0, 0.0], [0.75, math.sqrt(1 - 0.75**2)]), (None, None), True, 0.75),
        (([1.0, 0.0], [0.749, math.sqrt(1 - 0.749**2)]), (None, None), True, 0.749),
        (([1.0, 0.0], [-1.0, 0.0]), ([1.0, 0.0], [1.0, 0.0]), True, 0.0),
        ((None, None), ([1.0, 0.0], [1.0, 0.0]), True, 1.0),
        (([0.0, 0.0], [1.0, 0.0]), ([1.0, 0.0], [1.0, 0.0]), True, 1.0),
        (([1.0, 0.0], [1.0, 0.0]), (None, None), False, 0.89),
        ((None, None), (None, None), True, None),
        (([0.0, 0.0], [1.0, 0.0]), (None, None), True, None),
        (([1.0, 0.0], [1.0]), (None, None), True, None),
        (([float("nan"), 0.0], [1.0, 0.0]), (None, None), True, None),
        (([float("inf"), 0.0], [1.0, 0.0]), (None, None), True, None),
    ],
)
def test_display_similarity_uses_measurable_raw_cosine(text_pair, image_pair, bbox, expected):
    source = SimpleNamespace(id=1, embedding=text_pair[0], image_embedding=image_pair[0], text="exam")
    candidate = SimpleNamespace(id=2, embedding=text_pair[1], image_embedding=image_pair[1], meta={"bbox": [0, 0, 1, 1]} if bbox else {})
    similarity = _compute_display_sim(source, candidate)
    if expected is None:
        assert similarity is None
    else:
        assert similarity == pytest.approx(expected)
    entry = SimpleNamespace(selected_problem_ids=[candidate.id])
    statistics = _calculate_hit_statistics([source], {source.id: entry}, {candidate.id: candidate})
    assert statistics["hit_count"] == int(expected is not None and expected >= 0.75)
    assert statistics["curated_count"] == 1
    assert entry.selected_problem_ids == [candidate.id]


@pytest.mark.django_db
@pytest.mark.parametrize("miss_embedding", ([0.0, 1.0], [0.5, math.sqrt(0.75)]))
def test_hit_statistics_use_similarity_threshold_and_excluded_denominator(miss_embedding):
    Tenant = apps.get_model("core", "Tenant")
    InventoryFile = apps.get_model("inventory", "InventoryFile")

    suffix = uuid4().hex[:8]
    tenant = Tenant.objects.create(code=f"showcase-stats-{suffix}", name="Showcase stats")

    def make_document(name: str) -> MatchupDocument:
        inventory = InventoryFile.objects.create(
            tenant=tenant,
            scope="admin",
            student_ps="",
            display_name=f"{name}.pdf",
            r2_key=f"tests/showcase-stats/{suffix}/{name}.pdf",
            original_name=f"{name}.pdf",
            content_type="application/pdf",
            size_bytes=1,
        )
        return MatchupDocument.objects.create(
            tenant=tenant,
            inventory_file=inventory,
            title=name,
            r2_key=inventory.r2_key,
            original_name=inventory.original_name,
            content_type=inventory.content_type,
            size_bytes=inventory.size_bytes,
            status="done",
        )

    exam_document = make_document("exam")
    lesson_document = make_document("lesson")
    exam_problems = [
        MatchupProblem.objects.create(
            tenant=tenant,
            document=exam_document,
            number=number,
            text=f"exam {number}",
            image_key=f"tests/exam-{number}.png",
            embedding=embedding,
            meta={"bbox": [0, 0, 1, 1]},
        )
        for number, embedding in enumerate(
            ([1.0, 0.0], miss_embedding, [0.7, 0.7], [0.3, 0.7]),
            start=1,
        )
    ]
    hit_candidate = MatchupProblem.objects.create(
        tenant=tenant,
        document=lesson_document,
        number=1,
        text="hit",
        image_key="tests/hit.png",
        embedding=[1.0, 0.0],
        meta={"bbox": [0, 0, 1, 1]},
    )
    miss_candidate = MatchupProblem.objects.create(
        tenant=tenant,
        document=lesson_document,
        number=2,
        text="miss",
        image_key="tests/miss.png",
        embedding=[1.0, 0.0],
        meta={"bbox": [0, 0, 1, 1]},
    )
    report = MatchupHitReport.objects.create(
        tenant=tenant,
        document=exam_document,
        title="threshold report",
        status="submitted",
        submitted_by_name="박철",
    )
    foreign_tenant = Tenant.objects.create(code=f"foreign-stats-{suffix}", name="Other")
    foreign_candidate = MatchupProblem.objects.create(
        tenant=foreign_tenant,
        number=1,
        text="other tenant",
        image_key="tests/other.png",
        embedding=miss_embedding,
        meta={"bbox": [0, 0, 1, 1]},
    )
    selections = ([hit_candidate.id], [miss_candidate.id, foreign_candidate.id], [], [hit_candidate.id])
    for index, (exam_problem, selected_ids) in enumerate(
        zip(exam_problems, selections, strict=True),
    ):
        MatchupHitReportEntry.objects.create(
            tenant=tenant,
            report=report,
            exam_problem=exam_problem,
            selected_problem_ids=selected_ids,
            order=index,
            excluded=index == 3,
        )

    stored = list(report.entries.order_by("id").values("id", "selected_problem_ids", "excluded", "updated_at"))
    report_updated_at = report.updated_at
    statistics = calculate_matchup_hit_statistics(report)
    assert list(report.entries.order_by("id").values("id", "selected_problem_ids", "excluded", "updated_at")) == stored
    report.refresh_from_db()
    assert report.updated_at == report_updated_at

    assert statistics["total_questions"] == 3
    assert statistics["hit_count"] == 1
    assert statistics["hit_rate"] == pytest.approx(1 / 3)
    assert statistics["curated_count"] == 2
    assert statistics["curated_rate"] == pytest.approx(2 / 3)
