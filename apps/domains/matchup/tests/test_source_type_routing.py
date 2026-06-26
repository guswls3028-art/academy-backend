from academy.application.use_cases.ai.pipelines.matchup_pipeline import (
    _infer_source_type_from_names,
)
from apps.domains.matchup.source_types import normalize_source_type


def test_legacy_reference_maps_to_academy_workbook():
    assert normalize_source_type("reference") == "academy_workbook"


def test_infer_school_exam_before_segmentation_for_legacy_other():
    assert (
        _infer_source_type_from_names("other", "2026-1학기 중간고사.pdf", "")
        == "school_exam_pdf"
    )


def test_infer_skip_documents_before_segmentation_for_legacy_other():
    assert _infer_source_type_from_names("other", "정답표.pdf", "") == "answer_key"
    assert _infer_source_type_from_names("other", "해설지.pdf", "") == "explanation"


def test_explicit_source_type_is_not_overridden_by_filename():
    assert (
        _infer_source_type_from_names("academy_workbook", "2026 중간고사.pdf", "")
        == "academy_workbook"
    )
