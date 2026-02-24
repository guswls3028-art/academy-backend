# PATH: apps/support/video/management/commands/netprobe.py
"""
Network probe for Batch nodes: TCP to DB/REDIS, GET API health. Outputs JSON to stdout.
Used by academy-video-ops-netprobe job to prove Batch->RDS/Redis/API connectivity.
Reads DB_HOST, DB_PORT, REDIS_HOST, REDIS_PORT, API_BASE_URL from environment (SSM-injected).
If not set, fetches /academy/workers/env from SSM (job role has permission).
"""
from __future__ import annotations

import json
import os
import socket
import sys

from django.core.management.base import BaseCommand


def _load_env_from_ssm():
    """If DB_HOST not in env, try SSM /academy/workers/env."""
    if os.environ.get("DB_HOST"):
        return
    try:
        import boto3
        region = os.environ.get("AWS_DEFAULT_REGION", "ap-northeast-2")
        ssm = boto3.client("ssm", region_name=region)
        r = ssm.get_parameter(Name="/academy/workers/env", WithDecryption=True)
        payload = json.loads(r["Parameter"]["Value"])
        for k, v in (payload or {}).items():
            if isinstance(v, str) and k not in os.environ:
                os.environ[k] = v
    except Exception:
        pass


def _tcp_connect(host: str, port: int, timeout: float = 5.0) -> bool:
    try:
        s = socket.create_connection((host, port), timeout=timeout)
        s.close()
        return True
    except Exception:
        return False


def _http_get(url: str, timeout: float = 10.0) -> bool:
    try:
        from urllib.request import urlopen
        r = urlopen(url, timeout=timeout)
        return 200 <= r.getcode() < 400
    except Exception:
        return False


class Command(BaseCommand):
    help = "Probe DB, Redis, API connectivity; output JSON to stdout"

    def handle(self, *args, **options):
        _load_env_from_ssm()
        db_host = os.environ.get("DB_HOST", "").strip()
        db_port = int(os.environ.get("DB_PORT", "5432"))
        redis_host = os.environ.get("REDIS_HOST", "").strip()
        redis_port = int(os.environ.get("REDIS_PORT", "6379"))
        api_base = (os.environ.get("API_BASE_URL", "") or "").strip().rstrip("/")

        result = {}

        if db_host:
            result["db"] = "ok" if _tcp_connect(db_host, db_port) else "fail"
        else:
            result["db"] = "skip"

        if redis_host:
            result["redis"] = "ok" if _tcp_connect(redis_host, redis_port) else "fail"
        else:
            result["redis"] = "skip"

        if api_base:
            for path in ("/health", "/api/v1/health", "/"):
                url = api_base + path
                if _http_get(url):
                    result["api"] = "ok"
                    break
            else:
                result["api"] = "fail"
        else:
            result["api"] = "skip"

        json.dump(result, sys.stdout)
        sys.stdout.write("\n")
        if result.get("db") == "fail" or result.get("redis") == "fail" or result.get("api") == "fail":
            sys.exit(1)
        sys.exit(0)
