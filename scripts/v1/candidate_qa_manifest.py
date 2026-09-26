"""Immutable candidate QA root scope, separate from dynamic action receipts.

This validates metadata and its committed digest, not actual fixture creation.
Live preflight must independently verify creation seals and DB/storage state.
The root never embeds the derived lease binding (which includes this root hash).
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re

from scripts.v1.candidate_qa_window import require

SCHEMA = "academy-candidate-qa-root/v1"
MAX_BYTES = 256 * 1024
ROOT_FIELDS = {"schema", "lease_id", "owner_task", "source_sha", "images", "scope",
               "creator", "tenants", "initial_fixtures", "domains", "storage"}
ROLES = {"api", "ai", "tools", "messaging"}
HEX64 = re.compile(r"[0-9a-f]{64}")


def canonical_bytes(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode()


def fingerprint(value):
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def _positive(value):
    return type(value) is int and value > 0


def _hex(value):
    return isinstance(value, str) and HEX64.fullmatch(value) is not None


def _unique_pairs(pairs):
    value = {}
    for key, item in pairs:
        require(key not in value, "Duplicate root manifest JSON key")
        value[key] = item
    return value


def owned_prefixes(tenant_id):
    require(_positive(tenant_id), "Positive disposable tenant required")
    return {f"tenants/{tenant_id}/", f"excel/{tenant_id}/",
            f"tenant-logos/{tenant_id}/", f"landing-public/reviews/{tenant_id}/",
            f"matchup-showcase-snapshots/tenant_{tenant_id}/"}


def validate_root(value, *, expected_bucket):
    require(isinstance(value, dict) and set(value) == ROOT_FIELDS
            and value["schema"] == SCHEMA, "Exact root manifest schema required")
    require(isinstance(expected_bucket, str)
            and re.fullmatch(r"academy-development-[a-z0-9-]+", expected_bucket),
            "Trusted development bucket required")
    require(isinstance(value["lease_id"], str) and isinstance(value["owner_task"], str)
            and isinstance(value["source_sha"], str)
            and re.fullmatch(r"[0-9a-f]{32}", value["lease_id"])
            and re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", value["owner_task"])
            and re.fullmatch(r"[0-9a-f]{40}", value["source_sha"]),
            "Root lease/owner/source identity invalid")
    images = value["images"]
    require(isinstance(images, dict) and set(images) == ROLES
            and all(isinstance(v, str) and re.fullmatch(r"sha256:[0-9a-f]{64}", v)
                    for v in images.values()), "Four immutable root images required")
    scope = value["scope"]
    require(isinstance(scope, list) and scope and all(type(v) is int for v in scope)
            and set(scope) <= {509, 511} and len(set(scope)) == len(scope),
            "Approved root scope required")
    domains = {"auth"} | ({"ppt", "matchup"} if 509 in scope else set()) | ({"omr"} if 511 in scope else set())
    require(isinstance(value["domains"], list) and set(value["domains"]) == domains
            and len(value["domains"]) == len(domains), "Root domain inventory incomplete")
    require(value["creator"] == "frontend-development-qa/v1",
            "Owned official fixture creator required")
    tenants = value["tenants"]
    require(isinstance(tenants, list) and tenants, "Initial disposable tenant inventory required")
    ids, codes, users = set(), set(), set()
    for row in tenants:
        require(isinstance(row, dict)
                and set(row) == {"id", "code", "user_ids", "creation_seal_sha256"}
                and _positive(row["id"]) and row["id"] not in ids
                and isinstance(row["code"], str)
                and re.fullmatch(r"qa-[a-z0-9-]{1,96}", row["code"])
                and row["code"] not in codes and _hex(row["creation_seal_sha256"]),
                "Ambiguous or unsealed disposable tenant")
        members = row["user_ids"]
        require(isinstance(members, list) and members and all(_positive(v) for v in members)
                and len(set(members)) == len(members) and not users.intersection(members),
                "Exact creator-owned user inventory required")
        ids.add(row["id"]); codes.add(row["code"]); users.update(members)
    fixtures = value["initial_fixtures"]
    require(isinstance(fixtures, list) and fixtures, "Approved initial fixture rows required")
    seen, fixture_tenants = set(), set()
    for row in fixtures:
        require(isinstance(row, dict)
                and set(row) == {"tenant_id", "model", "ids", "state_sha256"}
                and _positive(row["tenant_id"]) and row["tenant_id"] in ids
                and isinstance(row["model"], str)
                and re.fullmatch(r"[a-z][a-z0-9_]*\.[A-Z][A-Za-z0-9_]*", row["model"])
                and _hex(row["state_sha256"]), "Initial fixture metadata differs")
        key = (row["tenant_id"], row["model"])
        require(key not in seen, "Duplicate initial fixture model group")
        rows = row["ids"]
        require(isinstance(rows, list) and rows
                and all(_positive(v) or (isinstance(v, str) and
                        re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", v))
                        for v in rows)
                and len(set(rows)) == len(rows), "Exact initial fixture IDs required")
        seen.add(key); fixture_tenants.add(row["tenant_id"])
    require(fixture_tenants == ids, "A tenant is missing initial fixture state")
    storage = value["storage"]
    require(isinstance(storage, list) and len(storage) == len(ids),
            "Complete owned storage inventory required")
    seen = set()
    for row in storage:
        require(isinstance(row, dict) and set(row) == {"tenant_id", "bucket", "prefixes"}
                and _positive(row["tenant_id"]) and row["tenant_id"] in ids and row["tenant_id"] not in seen
                and row["bucket"] == expected_bucket, "Storage tenant or bucket differs")
        prefixes = row["prefixes"]
        require(isinstance(prefixes, list) and len(prefixes) == len(set(prefixes))
                and set(prefixes) == owned_prefixes(row["tenant_id"]),
                "Complete exact tenant storage prefixes required")
        seen.add(row["tenant_id"])
    require(len(canonical_bytes(value)) <= MAX_BYTES, "Root manifest exceeds size bound")
    return value


@dataclass(frozen=True)
class RootManifest:
    sha256: str
    canonical_json: str

    @property
    def data(self):
        return json.loads(self.canonical_json)

    @property
    def initial_fixtures_sha256(self):
        rows = self.data["initial_fixtures"]
        for row in rows:
            row["ids"] = sorted(row["ids"], key=lambda v: (type(v).__name__, str(v)))
        return fingerprint(sorted(rows, key=lambda row: (row["tenant_id"], row["model"])))


def encode_root(value, *, expected_bucket):
    """Creator-side encoding only; never a substitute for actual creation/seal."""
    return canonical_bytes(validate_root(value, expected_bucket=expected_bucket))


def load_root(path, record, *, expected_bucket):
    path = Path(path)
    require(path.is_file() and not path.is_symlink() and path.stat().st_size <= MAX_BYTES,
            "Exact regular root manifest file required")
    raw = path.read_bytes()
    require(len(raw) <= MAX_BYTES and _hex(record.get("resource_manifest_sha256"))
            and hashlib.sha256(raw).hexdigest() == record["resource_manifest_sha256"],
            "Root manifest does not match the committed digest")
    value = json.loads(raw, object_pairs_hook=_unique_pairs)
    validate_root(value, expected_bucket=expected_bucket)
    for key in ("lease_id", "owner_task", "source_sha", "images"):
        require(value[key] == record[key], "Root belongs to another lease/source/owner")
    require(sorted(value["scope"]) == sorted(record["scope"])
            and sorted(row["id"] for row in value["tenants"]) == sorted(record["tenant_ids"]),
            "Root acceptance scope or tenant identity differs")
    return RootManifest(record["resource_manifest_sha256"], canonical_bytes(value).decode())
