import json
import os

from scripts.generate_openapi_schema import (
    SCHEMA_PATH,
    SCHEMA_SETTINGS_MODULE,
    _configure_schema_settings,
)


def test_schema_generator_overrides_ambient_django_settings(monkeypatch):
    monkeypatch.setenv("DJANGO_SETTINGS_MODULE", "apps.api.config.settings.test")

    _configure_schema_settings()

    assert SCHEMA_SETTINGS_MODULE == "apps.api.config.settings.schema"
    assert os.environ["DJANGO_SETTINGS_MODULE"] == SCHEMA_SETTINGS_MODULE


def test_enrollment_excel_schema_matches_async_multipart_contract():
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    operation = schema["paths"]["/api/v1/enrollments/lecture_enroll_from_excel/"]["post"]

    assert set(operation["requestBody"]["content"]) == {"multipart/form-data"}
    request_schema = operation["requestBody"]["content"]["multipart/form-data"]["schema"]
    request_component = schema["components"]["schemas"][request_schema["$ref"].rsplit("/", 1)[-1]]
    assert request_component["required"] == ["file", "lecture_id"]
    assert request_component["properties"]["file"]["format"] == "binary"
    assert request_component["properties"]["lecture_id"]["minimum"] == 1
    assert request_component["properties"]["session_id"]["minimum"] == 1

    assert set(operation["responses"]) == {"202"}
    response_schema = operation["responses"]["202"]["content"]["application/json"]["schema"]
    response_component = schema["components"]["schemas"][response_schema["$ref"].rsplit("/", 1)[-1]]
    assert response_component["required"] == ["job_id", "status"]
    assert response_component["properties"]["job_id"]["type"] == "string"
    status_schema = response_component["properties"]["status"]
    status_ref = status_schema["allOf"][0]["$ref"].rsplit("/", 1)[-1]
    assert schema["components"]["schemas"][status_ref]["enum"] == ["PENDING"]


def test_staff_submission_get_contracts_keep_legacy_arrays_and_scoped_review_modes():
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    components = schema["components"]["schemas"]
    cases = [
        ("pending/", "PendingExamSubmission", "PendingExamSubmissionPage",
         {"filter", "page", "page_size", "failed_type", "session_choices"}),
        ("exams/{exam_id}/", "StaffExamSubmission", "StaffExamReviewIssues",
         {"focus_id", "session_id", "review_issues", "cursor", "enrollment_ids", "include_unbound"}),
        ("homework/{homework_id}/", "StaffHomeworkSubmission", "StaffHomeworkSubmissionsPage",
         {"enrollment_id", "page", "page_size"}),
    ]
    for suffix, row, envelope, query in cases:
        operation = schema["paths"][f"/api/v1/submissions/submissions/{suffix}"]["get"]
        assert {p["name"] for p in operation["parameters"] if p["in"] == "query"} == query
        responses = operation["responses"]
        assert {"400", "401", "403"} <= responses.keys()
        union = responses["200"]["content"]["application/json"]["schema"]["$ref"].split("/")[-1]
        alternatives = components[union]["oneOf"]
        assert {"type": "array", "items": {"$ref": f"#/components/schemas/{row}"}} in alternatives
        assert {"$ref": f"#/components/schemas/{envelope}"} in alternatives
        assert components[row]["required"]
    assert set(components["StaffExamReviewIssues"]["required"]) == {"items", "total", "next_cursor"}
    assert set(components["StaffHomeworkSubmissionsPage"]["required"]) == {"results", "count", "next", "previous"}
    assert "summary" in components["PendingExamSubmissionPage"]["required"]
    assert components["StaffExamSubmission"]["properties"]["score"]["nullable"] is True


def test_results_dashboard_and_pending_inbox_publish_optional_session_provenance():
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    operation = schema["paths"]["/api/v1/results/admin/landing-stats/"]["get"]
    option = next(p for p in operation["parameters"] if p["name"] == "session_choices")
    assert set(option["schema"]["enum"]) == {"0", "1"}
    assert option.get("required", False) is False
    components = schema["components"]["schemas"]
    stats_name = operation["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].split("/")[-1]
    stats = components[stats_name]
    assert {"pending_top", "recent_done_top"} <= set(stats["required"])
    pending = stats["properties"]["pending_top"]["items"]
    assert stats["properties"]["recent_done_top"]["items"] == pending
    summary_name = pending["$ref"].split("/")[-1]
    optional_fields = {"session_choices", "session_title", "session_origin_recorded", "score_lecture_id", "session_history_only"}
    for name in (summary_name, "PendingExamSubmission"):
        row = components[name]
        assert optional_fields <= row["properties"].keys()
        assert optional_fields.isdisjoint(row["required"])
        assert row["properties"]["session_id"]["nullable"] is True
