#!/usr/bin/env python3
"""
Worker 금지 import 검사

apps/worker/** 내에서 다음 import 금지:
- apps.api
- rest_framework
- *views, *serializers (표현 계층)
- django.urls, django.conf.urls (라우팅)

있으면 exit 1
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORKER_ROOT = ROOT / "apps" / "worker"

FORBIDDEN_PATTERNS = [
    "apps.api",
    "rest_framework",
    "rest_framework.",
    ".views",
    ".serializers",
    "django.urls",
    "django.conf.urls",
]
EXCLUDES = {"__pycache__", ".git", "ai_dumps_backend"}


def check_file(path: Path) -> list[tuple[int, str]]:
    errors = []
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return []
    for i, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        for pat in FORBIDDEN_PATTERNS:
            if pat in line and ("import" in line or "from" in line):
                # .views/.serializers는 모듈 경로 일부인 경우만 (예: xxx.views)
                if pat in (".views", ".serializers"):
                    if " from " in line or " import " in line:
                        errors.append((i, f"forbidden: {pat!r} in {line.strip()[:80]}"))
                else:
                    errors.append((i, f"forbidden: {pat!r} in {line.strip()[:80]}"))
                break
    return errors


def main() -> int:
    errors = []
    for py in WORKER_ROOT.rglob("*.py"):
        if any(ex in str(py) for ex in EXCLUDES):
            continue
        rel = py.relative_to(ROOT)
        for line_no, msg in check_file(py):
            errors.append(f"  {rel}:{line_no} {msg}")
    if errors:
        print("ERROR: Worker forbidden imports found:")
        for e in errors[:30]:
            print(e)
        if len(errors) > 30:
            print(f"  ... and {len(errors) - 30} more")
        return 1
    print("OK: No forbidden imports in apps/worker/**")
    return 0


if __name__ == "__main__":
    sys.exit(main())
