from apps.domains.matchup.source_types import resolve_upload_source_type


def test_upload_source_type_wins_over_legacy_reference_intent():
    assert resolve_upload_source_type("academy_workbook", "reference") == "academy_workbook"


def test_upload_source_type_wins_over_legacy_test_intent():
    assert resolve_upload_source_type("school_exam_pdf", "test") == "school_exam_pdf"


def test_upload_intent_remains_fallback_for_legacy_clients():
    assert resolve_upload_source_type(None, "test") == "school_exam_pdf"
    assert resolve_upload_source_type("", "reference") == "academy_workbook"
