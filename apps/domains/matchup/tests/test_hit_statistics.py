from uuid import uuid4

import pytest
from django.apps import apps

from apps.domains.matchup.models import (
    MatchupDocument,
    MatchupHitReport,
    MatchupHitReportEntry,
    MatchupProblem,
)
from apps.domains.matchup.pdf_report import calculate_matchup_hit_statistics


@pytest.mark.django_db
def test_hit_statistics_use_similarity_threshold_and_excluded_denominator():
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
            ([1.0, 0.0], [0.0, 1.0], [0.7, 0.7], [0.3, 0.7]),
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
    selections = ([hit_candidate.id], [miss_candidate.id], [], [hit_candidate.id])
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

    statistics = calculate_matchup_hit_statistics(report)

    assert statistics["total_questions"] == 3
    assert statistics["hit_count"] == 1
    assert statistics["hit_rate"] == pytest.approx(1 / 3)
    assert statistics["curated_count"] == 2
    assert statistics["curated_rate"] == pytest.approx(2 / 3)
