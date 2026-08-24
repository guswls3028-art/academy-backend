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
