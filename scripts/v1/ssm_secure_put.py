"""Publish one SecureString from stdin without exposing its value to argv/logs."""

from __future__ import annotations

import contextlib
import io
import json
import sys


class SecurePutError(ValueError):
    """Only a fixed public error code may cross the process boundary."""


ERROR_CODES = frozenset({
    "SSM_SECURE_PUT_INVALID_REQUEST", "SSM_SECURE_PUT_SDK_UNAVAILABLE",
    "SSM_SECURE_PUT_INVALID_RESPONSE", "SSM_SECURE_PUT_FAILED",
})


def validate_request(envelope: object) -> tuple[dict, str | None, str | None]:
    if not isinstance(envelope, dict) or set(envelope) - {"request", "region", "profile"}:
        raise SecurePutError("SSM_SECURE_PUT_INVALID_REQUEST")
    request = envelope.get("request")
    required = {"Name", "Type", "Value", "Overwrite"}
    optional = {"Description", "Tier", "KeyId"}
    if (not isinstance(request, dict) or not required <= request.keys()
            or request.keys() - required - optional
            or request.get("Type") != "SecureString"
            or not isinstance(request.get("Overwrite"), bool)
            or any(not isinstance(request.get(k), str) or not request[k]
                   for k in ("Name", "Value"))
            or any(not isinstance(request[k], str) for k in optional & request.keys())):
        raise SecurePutError("SSM_SECURE_PUT_INVALID_REQUEST")
    if "Tier" in request and request["Tier"] not in {"Standard", "Advanced", "Intelligent-Tiering"}:
        raise SecurePutError("SSM_SECURE_PUT_INVALID_REQUEST")
    for key in ("region", "profile"):
        if envelope.get(key) is not None and not isinstance(envelope[key], str):
            raise SecurePutError("SSM_SECURE_PUT_INVALID_REQUEST")
    return request, envelope.get("region") or None, envelope.get("profile") or None


def put_secure_parameter(envelope: object, session_factory=None) -> dict:
    request, region, profile = validate_request(envelope)
    if session_factory is None:
        try:
            import boto3
            from botocore.config import Config
        except ImportError:
            raise SecurePutError("SSM_SECURE_PUT_SDK_UNAVAILABLE") from None
        # An ambiguous write must be read back by the caller, not retried here.
        session = boto3.Session(region_name=region, profile_name=profile)
        client = session.client("ssm", config=Config(
            retries={"total_max_attempts": 1}, connect_timeout=10, read_timeout=60,
        ))
    else:
        client = session_factory(region_name=region, profile_name=profile).client("ssm")
    result = client.put_parameter(**request)
    version = result.get("Version") if isinstance(result, dict) else None
    if type(version) is not int or version < 1:
        raise SecurePutError("SSM_SECURE_PUT_INVALID_RESPONSE")
    response = {"Version": version}
    if "Tier" in result:
        if result["Tier"] not in {"Standard", "Advanced", "Intelligent-Tiering"}:
            raise SecurePutError("SSM_SECURE_PUT_INVALID_RESPONSE")
        response["Tier"] = result["Tier"]
    return response


def main() -> int:
    try:
        # SDK diagnostics are never part of this small metadata protocol.
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            try:
                envelope = json.load(sys.stdin)
            except (ValueError, UnicodeError):
                raise SecurePutError("SSM_SECURE_PUT_INVALID_REQUEST") from None
            response = put_secure_parameter(envelope)
    except SecurePutError as exc:
        print(str(exc) if str(exc) in ERROR_CODES else "SSM_SECURE_PUT_FAILED", file=sys.stderr)
        return 2
    except Exception:
        print("SSM_SECURE_PUT_FAILED", file=sys.stderr)
        return 2
    print(json.dumps(response, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
