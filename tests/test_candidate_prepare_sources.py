"""Candidate preparation must prove isolated source ownership before writes."""

from datetime import date, timedelta

import pytest

from scripts.v1 import candidate_prepare_sources as contract


def _metadata(stage: str) -> dict[str, dict]:
    result = {}
    for kind, name in contract.SOURCES[stage].items():
        tags = {}
        if kind in {"api_base", "workers_base"}:
            tags = {
                "Environment": stage,
                "Purpose": "candidate-base-config",
                "SchemaVersion": "1",
                "Owner": "academy-release-ops",
            }
        if kind == "gemini_key":
            tags = {
                "Environment": "development",
                "Purpose": "matchup-synthetic-qa",
                "Owner": "academy-release-ops",
                "RotationOwner": "provider-key-operator",
                "GoogleProjectId": "academy-dev-example",
                "RotateBy": (date.today() + timedelta(days=30)).isoformat(),
            }
        result[name] = {"Name": name, "Type": "SecureString", "Version": 1, "Tags": tags}
    return result


def test_candidate_source_names_and_key_classes_exclude_production_blobs():
    names = {name for sources in contract.SOURCES.values() for name in sources.values()}
    assert "/academy/api/env" not in names
    assert "/academy/workers/env" not in names
    assert "/academy/api/development/env" not in names
    assert "/academy/workers/development/env" not in names
    assert "/academy/api/preprod/env" not in names
    assert len(names) == sum(len(sources) for sources in contract.SOURCES.values())
    classes = list(contract.KEY_CLASSES.values())
    assert all(not (left & right) for i, left in enumerate(classes) for right in classes[i + 1:])
    assert "GEMINI_API_KEY" in contract.KEY_CLASSES["provider_secret"]
    assert "GEMINI_API_KEY" not in contract.KEY_CLASSES["nonsecret_base"]


def test_candidate_sources_fail_closed_on_missing_or_unowned_inputs():
    development = _metadata("development")
    assert contract.validate_metadata("development", development)["gemini_key"] == 1
    assert contract.validate_metadata("preprod", _metadata("preprod"))["api_base"] == 1

    missing = dict(development)
    del missing[contract.SOURCES["development"]["api_base"]]
    with pytest.raises(contract.CandidateSourceUnavailable, match="Missing exact"):
        contract.validate_metadata("development", missing)

    unowned = _metadata("development")
    unowned[contract.SOURCES["development"]["api_base"]]["Tags"].pop("Owner")
    with pytest.raises(contract.CandidateSourceUnavailable, match="owner/schema"):
        contract.validate_metadata("development", unowned)

    expired = _metadata("development")
    expired[contract.SOURCES["development"]["gemini_key"]]["Tags"]["RotateBy"] = (
        date.today() - timedelta(days=1)
    ).isoformat()
    with pytest.raises(contract.CandidateSourceUnavailable, match="outside 90 days"):
        contract.validate_metadata("development", expired)

    unassigned = _metadata("development")
    unassigned[contract.SOURCES["development"]["gemini_key"]]["Tags"]["Owner"] = "TBD"
    with pytest.raises(contract.CandidateSourceUnavailable, match="provider owner"):
        contract.validate_metadata("development", unassigned)


def test_candidate_base_accepts_only_named_nonsecret_settings():
    contract.validate_base_keys(["AWS_DEFAULT_REGION", "DB_HOST", "DB_PORT"])
    for invalid in ("GEMINI_API_KEY", "DB_PASSWORD", "SOLAPI_API_KEY", "UNKNOWN"):
        with pytest.raises(contract.CandidateSourceUnavailable, match="unclassified"):
            contract.validate_base_keys(["AWS_DEFAULT_REGION", "DB_HOST", "DB_PORT", invalid])
    with pytest.raises(contract.CandidateSourceUnavailable, match="missing"):
        contract.validate_base_keys(["AWS_DEFAULT_REGION", "DB_HOST"])


def test_candidate_preflight_reads_metadata_only():
    data = _metadata("development")

    class MetadataOnlySSM:
        def describe_parameters(self, *, ParameterFilters):
            name = ParameterFilters[0]["Values"][0]
            return {"Parameters": [{k: v for k, v in data[name].items() if k != "Tags"}]}

        def list_tags_for_resource(self, *, ResourceType, ResourceId):
            assert ResourceType == "Parameter"
            return {"TagList": [
                {"Key": key, "Value": value}
                for key, value in data[ResourceId]["Tags"].items()
            ]}

        def get_parameter(self, **_kwargs):
            raise AssertionError("Preflight must not read secret values")

        def put_parameter(self, **_kwargs):
            raise AssertionError("Preflight must not publish environments")

    metadata = contract.read_metadata(MetadataOnlySSM(), "development")
    assert contract.validate_metadata("development", metadata)["gemini_key"] == 1
