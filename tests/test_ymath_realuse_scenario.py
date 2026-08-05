from pathlib import Path

import pytest

from scripts import ymath_realuse_scenario
from scripts.ymath_realuse_scenario import (
    assert_safe_target,
    build_source_plan,
    execute_item,
)


def test_build_source_plan_keeps_all_routes_explicit(tmp_path: Path):
    manifest = {
        "documents": [
            {
                "source_id": "pdf",
                "extension": ".pdf",
                "extracted_path": str(tmp_path / "problems.pdf"),
            },
            {
                "source_id": "combined",
                "extension": ".hwp",
                "extracted_path": str(tmp_path / "combined.hwp"),
            },
            {
                "source_id": "paired",
                "extension": ".hwpx",
                "extracted_path": str(tmp_path / "teacher.hwpx"),
            },
        ]
    }
    qa = {
        "items": [
            {
                "source_id": "combined",
                "status": "combined_document_ready",
                "control_count": 20,
            },
            {
                "source_id": "paired",
                "status": "paired_problem_file_required",
                "control_count": 10,
                "visual_count": 8,
                "missing_visual_numbers": [9, 10],
            },
        ]
    }

    blocked = build_source_plan(manifest, qa)
    paired = build_source_plan(
        manifest,
        qa,
        {"paired": str(tmp_path / "clean-problems.pdf")},
    )

    assert [item["route"] for item in blocked] == [
        "problem_only",
        "combined_document",
        "blocked",
    ]
    assert blocked[2]["reason"] == "clean_problem_pdf_required"
    assert blocked[2]["upload_path"].endswith("teacher.hwpx")
    assert blocked[2]["expected_execution_status"] == "conversion_required"
    assert paired[2]["route"] == "paired_problem_and_explanation"
    assert paired[2]["extracted_explanation_count"] == 8


def test_build_source_plan_groups_problem_and_explanation_sources(tmp_path: Path):
    manifest = {
        "documents": [
            {
                "source_id": "problem-hwp",
                "extension": ".hwp",
                "extracted_path": str(tmp_path / "problems.hwp"),
            },
            {
                "source_id": "explanation-hwp",
                "extension": ".hwp",
                "extracted_path": str(tmp_path / "teacher.hwp"),
            },
        ]
    }
    qa = {
        "items": [
            {
                "source_id": "problem-hwp",
                "status": "paired_problem_file_required",
                "control_count": 24,
                "visual_count": 8,
            },
            {
                "source_id": "explanation-hwp",
                "status": "paired_problem_file_required",
                "control_count": 24,
                "visual_count": 23,
            },
        ]
    }
    pairings = {
        "problem-hwp": {
            "problem_path": str(tmp_path / "clean-problems.pdf"),
            "explanation_source_id": "explanation-hwp",
            "consumed_source_ids": ["problem-hwp", "explanation-hwp"],
        }
    }

    plan = build_source_plan(manifest, qa, pairings)

    assert plan[0]["route"] == "paired_problem_and_explanation"
    assert plan[0]["explanation_path"].endswith("teacher.hwp")
    assert plan[0]["extracted_explanation_count"] == 23
    assert plan[1]["route"] == "consumed_by_pair"
    assert plan[1]["consumed_by"] == "problem-hwp"


@pytest.mark.parametrize(
    "url,tenant",
    [
        ("https://api.hakwonplus.com", "qa-ymath-realuse-20260805"),
        ("http://127.0.0.1:18000", "ymath"),
    ],
)
def test_assert_safe_target_rejects_production_or_real_tenant(url: str, tenant: str):
    with pytest.raises(ValueError):
        assert_safe_target(url, tenant)


def test_assert_safe_target_accepts_loopback_development_tunnel():
    assert_safe_target(
        "http://127.0.0.1:18000",
        "qa-ymath-realuse-20260805",
    )


def test_execute_item_fails_closed_on_partial_teacher_explanations(monkeypatch):
    class Client:
        @staticmethod
        def get_json(path):
            assert path == "/api/v1/exams/31/segmentation-review/"
            return {
                "status": "review_required",
                "items": [
                    {"has_teacher_explanation": True},
                    {"has_teacher_explanation": False},
                ],
            }

    monkeypatch.setattr(
        ymath_realuse_scenario,
        "_wait_for_job",
        lambda *_args, **_kwargs: {"status": "DONE", "result": {}},
    )

    result = execute_item(
        client=Client(),
        item={
            "source_id": "paired",
            "route": "paired_problem_and_explanation",
            "detected_question_count": 2,
        },
        session_id=1,
        job_timeout=1,
        prior={"product_type": "exam", "exam_id": 31, "job_id": "job-31"},
        checkpoint=lambda _state: None,
    )

    assert result["proposal_count"] == 2
    assert result["teacher_explanation_count"] == 1
    assert result["execution_status"] == "teacher_explanation_coverage_incomplete"


def test_execute_item_records_expected_conversion_as_remediation(monkeypatch):
    class Client:
        @staticmethod
        def get_json(path):
            assert path == "/api/v1/exams/32/segmentation-review/"
            return {"status": "conversion_required", "items": []}

    monkeypatch.setattr(
        ymath_realuse_scenario,
        "_wait_for_job",
        lambda *_args, **_kwargs: {
            "status": "DONE",
            "result": {"conversion_required": True},
        },
    )

    result = execute_item(
        client=Client(),
        item={
            "source_id": "annotated-workbook",
            "route": "blocked",
            "expected_execution_status": "conversion_required",
        },
        session_id=1,
        job_timeout=1,
        prior={"product_type": "homework", "exam_id": 32, "job_id": "job-32"},
        checkpoint=lambda _state: None,
    )

    assert result["proposal_count"] == 0
    assert result["execution_status"] == "source_remediation_required"
