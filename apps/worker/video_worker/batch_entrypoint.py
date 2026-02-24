#!/usr/bin/env python3
"""
Batch entrypoint: fetch /academy/workers/env from SSM, set os.environ, then exec.
Supports SSM value as JSON (production) or legacy KEY=VALUE lines.
Job role must have ssm:GetParameter for academy/*.
"""
from __future__ import annotations

import json
import os
import re
import sys

REGION = os.environ.get("AWS_DEFAULT_REGION") or os.environ.get("AWS_REGION") or "ap-northeast-2"
SSM_NAME = os.environ.get("BATCH_SSM_ENV", "/academy/workers/env")

# Keys whose values must not be logged
SECRET_KEYS = frozenset({
    "DB_PASSWORD", "R2_SECRET_KEY", "SECRET_KEY", "INTERNAL_WORKER_TOKEN",
    "LAMBDA_INTERNAL_API_KEY", "SOLAPI_API_KEY", "SOLAPI_API_SECRET",
    "REDIS_PASSWORD",
})


def _mask(key: str, value: str) -> str:
    if key in SECRET_KEYS and value:
        return "***"
    return value


def _parse_key_val_lines(content: str) -> dict[str, str]:
    """Legacy: parse KEY=VALUE lines. Returns dict of key -> value."""
    out: dict[str, str] = {}
    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$", line)
        if m:
            key, val = m.group(1), m.group(2).strip()
            if (val.startswith("'") and val.endswith("'")) or (val.startswith('"') and val.endswith('"')):
                val = val[1:-1].replace("\\'", "'").replace('\\"', '"')
            out[key] = val
    return out


def _load_env_from_ssm_value(content: str) -> tuple[int, bool]:
    """
    Load environment from SSM value. Prefer JSON; fallback to KEY=VALUE lines.
    Returns (number of keys set, True if JSON was used).
    """
    content = (content or "").strip()
    if not content:
        raise RuntimeError("SSM parameter value is empty")

    # 1) Try JSON
    try:
        data = json.loads(content)
        if not isinstance(data, dict):
            raise ValueError("SSM JSON must be a JSON object")
        for k, v in data.items():
            if not isinstance(k, str):
                continue
            os.environ[k] = str(v) if v is not None else ""
        return len(data), True
    except json.JSONDecodeError:
        pass

    # 2) Fallback: KEY=VALUE lines
    parsed = _parse_key_val_lines(content)
    if not parsed:
        raise RuntimeError(
            "SSM value is neither valid JSON nor KEY=VALUE lines; no env loaded"
        )
    for k, v in parsed.items():
        os.environ[k] = v
    return len(parsed), False


def main() -> int:
    try:
        import boto3
    except ImportError as e:
        print(f"batch_entrypoint: boto3 required: {e}", file=sys.stderr)
        return 1

    try:
        client = boto3.client("ssm", region_name=REGION)
        r = client.get_parameter(Name=SSM_NAME, WithDecryption=True)
        content = r["Parameter"]["Value"]
    except Exception as e:
        print(f"batch_entrypoint: SSM fetch failed: {e}", file=sys.stderr)
        return 1

    try:
        n, from_json = _load_env_from_ssm_value(content)
        if from_json:
            print(f"Loaded SSM JSON with {n} keys", file=sys.stderr)
        else:
            print(f"Loaded SSM env with {n} keys (legacy)", file=sys.stderr)
    except (RuntimeError, ValueError) as e:
        print(f"batch_entrypoint: {e}", file=sys.stderr)
        return 1

    # Assert DJANGO_SETTINGS_MODULE
    dsm = os.environ.get("DJANGO_SETTINGS_MODULE", "").strip()
    if not dsm:
        os.environ.setdefault(
            "DJANGO_SETTINGS_MODULE",
            "apps.api.config.settings.worker",
        )
        dsm = os.environ["DJANGO_SETTINGS_MODULE"]
    print(f"DJANGO_SETTINGS_MODULE = {dsm}", file=sys.stderr)

    # CMD from Batch is passed as args
    argv = sys.argv[1:] if len(sys.argv) > 1 else ["python", "-m", "apps.worker.video_worker.batch_main"]
    if argv[0] == "python" or argv[0].endswith("python"):
        os.execvp(argv[0], argv)
    else:
        os.execv(argv[0], argv)
    return 0


if __name__ == "__main__":
    sys.exit(main())
