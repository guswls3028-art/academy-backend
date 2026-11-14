import json
import math
from types import SimpleNamespace
from uuid import uuid4

import pytest
from django.apps import apps
from django.db import connection
from django.test import RequestFactory
from django.test.utils import CaptureQueriesContext

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
from apps.domains.matchup.views_hit_report import HitReportDraftView


@pytest.mark.django_db
def test_editor_separates_rank_and_report_scores_and_keeps_manual_pins(monkeypatch):
    tenant = apps.get_model("core", "Tenant").objects.create(code="display-sim", name="Display sim")
    InventoryFile = apps.get_model("inventory", "InventoryFile")

    def document(name):
        inventory = InventoryFile.objects.create(
            tenant=tenant, scope="admin", display_name=f"{name}.pdf",
            r2_key=f"display-sim/{name}.pdf", original_name=f"{name}.pdf",
        )
        return MatchupDocument.objects.create(
            tenant=tenant, inventory_file=inventory, title=name, r2_key=inventory.r2_key,
        )

    exam, lesson = document("exam"), document("lesson")
    question = MatchupProblem.objects.create(
        tenant=tenant, document=exam, number=1, text="short question", embedding=[1.0, 0.0],
        image_embedding=[1.0, 0.0],
    )
    choice_vector = [0.658, math.sqrt(1 - 0.658**2)]
    choice = MatchupProblem.objects.create(
        tenant=tenant, document=lesson, number=1, embedding=choice_vector,
        image_embedding=choice_vector, meta={"manual": True, "bbox_norm": [0, 0, 1, 1]},
    )
    unreadable = MatchupProblem.objects.create(tenant=tenant, document=lesson, number=2)
    fallback = MatchupProblem.objects.create(
        tenant=tenant, document=lesson, number=3, embedding=[1.0, 0.0],
    )
    identical = MatchupProblem.objects.create(
        tenant=tenant, document=lesson, number=4, embedding=[1.0, 0.0],
        image_embedding=[1.0, 0.0], meta={"bbox": [0, 0, 1, 1]},
    )
    report = MatchupHitReport.objects.create(tenant=tenant, document=exam, title="Report")
    entry = MatchupHitReportEntry.objects.create(
        tenant=tenant, report=report, exam_problem=question, selected_problem_ids=[choice.id, unreadable.id],
    )
    monkeypatch.setattr("apps.domains.matchup.views_hit_report._is_tenant_staff", lambda _: True)
    monkeypatch.setattr("apps.domains.matchup.views_hit_report.generate_presigned_get_url_storage", None)
    monkeypatch.setattr(
        "apps.domains.matchup.services.find_similar_problems",
        lambda **_: [(identical, 1.0), (choice, 0.829), (fallback, 0.8)],
    )

    def payload(**query):
        request = RequestFactory().get("/", query)
        request.tenant, request.user = tenant, None
        with CaptureQueriesContext(connection) as queries:
            response = HitReportDraftView().get(request, exam.id)
        assert response.status_code == 200
        vector_reads = [
            query["sql"] for query in queries
            if '"matchup_matchupproblem"."image_embedding"' in query["sql"]
        ]
        assert len(vector_reads) == (2 if query.get("mode") == "quick" else 3)
        return json.loads(response.content)["exam_problems"][0]

    for result in (payload(), payload(mode="candidates", problem_ids=str(question.id))):
        assert [c["id"] for c in result["candidates"]] == [identical.id, choice.id, fallback.id]
        assert result["candidates"][0]["display_similarity"] == 1.0
        assert result["candidates"][1]["similarity"] == 0.829
        assert result["candidates"][1]["display_similarity"] == pytest.approx(0.658)
        assert result["candidates"][2]["display_similarity"] == pytest.approx(0.89)
    for result in (payload(), payload(mode="quick")):
        assert result["entry"]["selected_display_similarity"] == {
            str(choice.id): pytest.approx(_compute_display_sim(question, choice)),
            str(unreadable.id): None,
        }
    entry.refresh_from_db()
    assert entry.selected_problem_ids == [choice.id, unreadable.id]


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
