"""Verify the setup seal for one disposable development QA tenant."""

import hashlib
import hmac


STANDARD_SETUP_SCHEMA = "frontend-development-qa/v1"
MULTIPART_SETUP_SCHEMA = "frontend-development-qa/video-multipart-v1"


def same_setup_owner(record, expected):
    return (
        isinstance(record, dict)
        and set(record) == set(expected)
        and all(
            type(record[key]) is type(value) and record[key] == value
            for key, value in expected.items()
            if key != "owner_sha256"
        )
        and isinstance(record["owner_sha256"], str)
        and hmac.compare_digest(record["owner_sha256"], expected["owner_sha256"])
    )


def verify_setup_owner(tenant_code, tenant_id, capability, record, *, schema=None):
    if not isinstance(record, dict):
        raise PermissionError("Invalid QA setup ownership")
    stored_schema = record.get("schema")
    if stored_schema not in (STANDARD_SETUP_SCHEMA, MULTIPART_SETUP_SCHEMA) or (
        schema is not None and stored_schema != schema
    ):
        raise PermissionError("Invalid QA setup ownership schema")

    owner_scope = f"{tenant_code}:{tenant_id}"
    expected = {"schema": stored_schema, "tenant_code": tenant_code, "tenant_id": tenant_id}
    if stored_schema == MULTIPART_SETUP_SCHEMA:
        session_id = record.get("multipart_session_id")
        if type(session_id) is not int or not 0 < session_id <= 9223372036854775807:
            raise PermissionError("Invalid multipart QA ownership session")
        expected["multipart_session_id"] = session_id
        owner_scope = f"{owner_scope}:video-multipart-v1:{session_id}"
    expected["owner_sha256"] = hashlib.sha256(f"{owner_scope}:{capability}".encode()).hexdigest()
    if not same_setup_owner(record, expected):
        raise PermissionError("QA setup ownership mismatch")
    return expected
