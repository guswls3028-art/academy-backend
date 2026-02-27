#!/usr/bin/env python
"""
배포용 .env 검증 — 코드 기준 필수 키 존재·비어있지 않음 확인.

사용: python scripts/validate_env.py [.env 또는 경로]
참조: apps/api/config/settings/base.py, apps/api/config/settings/worker.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# 배포(API + 워커) 시 반드시 있어야 하고 비어있으면 안 되는 키
REQUIRED = [
    "SECRET_KEY",
    "DB_NAME",
    "DB_USER",
    "DB_PASSWORD",
    "DB_HOST",
    "DB_PORT",
    "R2_ACCESS_KEY",
    "R2_SECRET_KEY",
    "R2_ENDPOINT",
    "R2_PUBLIC_BASE_URL",
    "INTERNAL_WORKER_TOKEN",
]

# 워커가 API 호출할 때 필요 (비어있으면 경고)
RECOMMENDED_FOR_WORKERS = ["API_BASE_URL"]


def parse_env(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.exists():
        return out
    raw = path.read_text(encoding="utf-8", errors="replace")
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$", line)
        if m:
            key, val = m.group(1), m.group(2).strip()
            if val.startswith('"') and val.endswith('"'):
                val = val[1:-1].replace('\\"', '"')
            out[key] = val
    return out


def main() -> int:
    path = ROOT / ".env"
    if len(sys.argv) >= 2:
        path = Path(sys.argv[1])
        if not path.is_absolute():
            path = ROOT / path

    if not path.exists():
        print(f"ERROR: {path} not found")
        return 1

    env = parse_env(path)
    missing = [k for k in REQUIRED if not env.get(k)]
    empty_required = [k for k in REQUIRED if k in env and env[k].strip() == ""]
    recommended_missing = [k for k in RECOMMENDED_FOR_WORKERS if not env.get(k) or not env.get(k).strip()]

    if missing:
        print(f"Missing required keys: {', '.join(missing)}")
    if empty_required:
        print(f"Required but empty: {', '.join(empty_required)}")
    if recommended_missing:
        print(f"Recommended for workers (empty or missing): {', '.join(recommended_missing)}")

    if missing or empty_required:
        print("\nSee .env.example for template (code ref: base.py, worker.py).")
        return 1

    if recommended_missing:
        print("\n(Workers need API_BASE_URL to call API; otherwise OK.)")
    else:
        print("OK: required env vars present and non-empty.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
