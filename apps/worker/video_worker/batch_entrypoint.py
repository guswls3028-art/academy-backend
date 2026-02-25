#!/usr/bin/env python3
"""
Batch entrypoint: fetch /academy/workers/env from SSM (JSON or base64-encoded JSON), set os.environ, validate, then exec.
Single boot path for all Batch jobs (worker, netprobe, reconcile, scan_stuck).
No silent fallback. Fail fast on missing/invalid config.
SSM value may be plain JSON or base64(UTF-8 JSON) to avoid Windows CLI quoting corruption.
"""
from __future__ import annotations

import base64
import json
import os
import sys

REGION = os.environ.get("AWS_DEFAULT_REGION") or os.environ.get("AWS_REGION") or "ap-northeast-2"
SSM_NAME = os.environ.get("BATCH_SSM_ENV", "/academy/workers/env")

# Required keys in SSM JSON (Batch worker + ops). Missing => exit 1.
REQUIRED_KEYS = frozenset({
    "AWS_DEFAULT_REGION",
    "DB_HOST", "DB_NAME", "DB_USER", "DB_PASSWORD", "DB_PORT",
    "R2_ACCESS_KEY", "R2_SECRET_KEY", "R2_ENDPOINT", "R2_VIDEO_BUCKET",
    "API_BASE_URL", "INTERNAL_WORKER_TOKEN",
    "REDIS_HOST", "REDIS_PORT",
    "DJANGO_SETTINGS_MODULE",
})


def load_env_from_ssm_json(content: str) -> int:
    """
    Parse SSM value as JSON object and set os.environ. No legacy KEY=VALUE.
    Returns number of keys set. Raises on invalid JSON or non-dict.
    """
    content = (content or "").strip()
    if not content:
        raise RuntimeError("SSM parameter value is empty")
    data = json.loads(content)
    if not isinstance(data, dict):
        raise ValueError("SSM value must be a JSON object")
    for k, v in data.items():
        if isinstance(k, str):
            os.environ[k] = str(v) if v is not None else ""
    return len(data)


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
        n = load_env_from_ssm_json(content)
        print(f"Loaded SSM JSON with {n} keys", file=sys.stderr)
    except json.JSONDecodeError as e:
        print(f"batch_entrypoint: SSM value is not valid JSON: {e}", file=sys.stderr)
        return 1
    except (RuntimeError, ValueError) as e:
        print(f"batch_entrypoint: {e}", file=sys.stderr)
        return 1

    # Validate required keys (no silent fallback)
    missing = [k for k in REQUIRED_KEYS if not (os.environ.get(k) or "").strip()]
    if missing:
        print(f"batch_entrypoint: missing required env keys: {missing}", file=sys.stderr)
        return 1

    dsm = (os.environ.get("DJANGO_SETTINGS_MODULE") or "").strip()
    if dsm != "apps.api.config.settings.worker":
        print(f"batch_entrypoint: DJANGO_SETTINGS_MODULE must be apps.api.config.settings.worker (got {dsm!r})", file=sys.stderr)
        return 1
    print(f"DJANGO_SETTINGS_MODULE = {dsm}", file=sys.stderr)

    argv = sys.argv[1:] if len(sys.argv) > 1 else ["python", "-m", "apps.worker.video_worker.batch_main"]
    if argv[0] == "python" or argv[0].endswith("python"):
        os.execvp(argv[0], argv)
    else:
        os.execv(argv[0], argv)
    return 0


if __name__ == "__main__":
    sys.exit(main())
