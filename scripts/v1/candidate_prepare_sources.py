"""Read-only source contract for a future isolated candidate preparation path.

This module never reads parameter values or publishes an environment. The
candidate publisher must validate these exact sources before any mutation and
must assemble its runtime environment from them, without legacy env fallbacks.
"""

from __future__ import annotations

import argparse
from datetime import date, timedelta


SOURCES = {
    "development": {
        "api_base": "/academy/api/development/base-env",
        "workers_base": "/academy/workers/development/base-env",
        "db_credentials": "/academy/api/development/db-credentials",
        "r2_credentials": "/academy/r2/development/credentials",
        "gemini_key": "/academy/providers/development/gemini-api-key",
    },
    "preprod": {
        "api_base": "/academy/api/preprod/base-env",
        "workers_base": "/academy/workers/preprod/base-env",
        "db_credentials": "/academy/api/preprod/db-credentials",
        "r2_credentials": "/academy/r2/preprod/credentials",
    },
}

# Key-name classification, not environment values. Expand the nonsecret
# allowlist only when a candidate runtime setting has a verified owner.
KEY_CLASSES = {
    "nonsecret_base": frozenset({
        "AWS_DEFAULT_REGION", "CDN_HLS_BASE_URL", "DB_HOST", "DB_PORT",
        "DB_SSL_MODE", "MATCHUP_HYBRID_VLM_TENANTS",
        "MATCHUP_VLM_FILL_EMPTY_PAGES", "MATCHUP_VLM_PAGE_ROLE_FILTER",
        "MATCHUP_VLM_TEXT_ADAPTER", "MATCHUP_VLM_VISION_ADAPTER", "SITE_URL",
    }),
    "credential_reference": frozenset({
        "DB_PASSWORD", "R2_ACCESS_KEY", "R2_SECRET_KEY",
    }),
    "provider_secret": frozenset({"GEMINI_API_KEY"}),
    "generated_runtime_identity": frozenset({
        "ACADEMY_DEVELOPMENT_RELEASE_ID", "ACADEMY_PREPROD_RELEASE_ID",
        "ACADEMY_RUNTIME_ENV", "DB_NAME", "DB_USER", "DJANGO_SETTINGS_MODULE",
        "MESSAGING_TENANT_BINDING_KEY", "SECRET_KEY",
    }),
    "prohibited_production_only": frozenset({
        "ANTHROPIC_API_KEY", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN", "OPENAI_API_KEY", "SOLAPI_API_KEY",
        "SOLAPI_API_SECRET", "TOSS_PAYMENTS_SECRET_KEY", "VAPID_PRIVATE_KEY",
    }),
}


class CandidateSourceUnavailable(ValueError):
    """An exact nonproduction source or its ownership proof is missing."""


def _assigned(value: object) -> bool:
    return str(value or "").strip().upper() not in {"", "TBD", "TODO", "PLACEHOLDER"}


def validate_base_keys(keys: set[str] | list[str]) -> None:
    unknown = set(keys) - KEY_CLASSES["nonsecret_base"]
    if unknown:
        raise CandidateSourceUnavailable(
            "Candidate base config has unclassified keys: " + ", ".join(sorted(unknown))
        )
    required = {"AWS_DEFAULT_REGION", "DB_HOST", "DB_PORT"}
    missing = required - set(keys)
    if missing:
        raise CandidateSourceUnavailable(
            "Candidate base config is missing keys: " + ", ".join(sorted(missing))
        )


def validate_metadata(stage: str, metadata: dict[str, dict]) -> dict[str, int]:
    """Validate names, types, versions and tags without accessing values."""
    if stage not in SOURCES:
        raise CandidateSourceUnavailable("Unknown candidate stage")
    versions = {}
    for kind, name in SOURCES[stage].items():
        item = metadata.get(name)
        if not item or item.get("Name") != name:
            raise CandidateSourceUnavailable(f"Missing exact candidate source: {name}")
        if item.get("Type") != "SecureString" or int(item.get("Version") or 0) < 1:
            raise CandidateSourceUnavailable(f"Invalid type/version for: {name}")
        tags = item.get("Tags") or {}
        if kind in {"api_base", "workers_base"}:
            if (tags.get("Environment") != stage or
                    tags.get("Purpose") != "candidate-base-config" or
                    tags.get("SchemaVersion") != "1" or
                    not _assigned(tags.get("Owner"))):
                raise CandidateSourceUnavailable(f"Missing base owner/schema tags for: {name}")
        if kind == "gemini_key":
            if (tags.get("Environment") != "development" or
                    tags.get("Purpose") != "matchup-synthetic-qa" or
                    not _assigned(tags.get("Owner")) or
                    not _assigned(tags.get("RotationOwner")) or
                    not _assigned(tags.get("GoogleProjectId"))):
                raise CandidateSourceUnavailable(f"Missing provider owner tags for: {name}")
            try:
                rotate_by = date.fromisoformat(tags["RotateBy"])
            except (KeyError, ValueError):
                raise CandidateSourceUnavailable(f"Missing provider rotation date for: {name}") from None
            if not date.today() <= rotate_by <= date.today() + timedelta(days=90):
                raise CandidateSourceUnavailable(f"Provider rotation date outside 90 days for: {name}")
        versions[kind] = int(item["Version"])
    return versions


def read_metadata(ssm, stage: str) -> dict[str, dict]:
    """Use only DescribeParameters and ListTagsForResource, never GetParameter."""
    if stage not in SOURCES:
        raise CandidateSourceUnavailable("Unknown candidate stage")
    metadata = {}
    for name in SOURCES[stage].values():
        result = ssm.describe_parameters(
            ParameterFilters=[{"Key": "Name", "Option": "Equals", "Values": [name]}]
        )
        matches = [item for item in result.get("Parameters", []) if item.get("Name") == name]
        if len(matches) != 1:
            raise CandidateSourceUnavailable(f"Missing exact candidate source: {name}")
        item = dict(matches[0])
        tags = ssm.list_tags_for_resource(ResourceType="Parameter", ResourceId=name)
        item["Tags"] = {tag["Key"]: tag["Value"] for tag in tags.get("TagList", [])}
        metadata[name] = item
    return metadata


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=tuple(SOURCES))
    parser.add_argument("--region", default="ap-northeast-2")
    args = parser.parse_args()
    import boto3

    ssm = boto3.client("ssm", region_name=args.region)
    try:
        versions = validate_metadata(args.stage, read_metadata(ssm, args.stage))
    except CandidateSourceUnavailable as exc:
        parser.exit(2, f"CANDIDATE_SOURCE_METADATA_BLOCKED: {exc}\n")
    print("CANDIDATE_SOURCE_METADATA_PASS stage=" + args.stage +
          " versions=" + ",".join(f"{kind}:{version}" for kind, version in versions.items()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
