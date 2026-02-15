#!/usr/bin/env python3
"""
CI validation: Fail if celery references exist in code/requirements/configs.
Redis는 정식 도입됨. Celery만 금지 (메시지 브로커로 사용하지 않음).
"""
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXCLUDES = {
    ".git",
    "node_modules",
    "__pycache__",
    ".venv",
    "venv",
    "LICENSE",
    "requirements/전체트리",
}
ALLOWED_PATTERNS = [
    r"Celery\s+제거",
    r"celery\s+removed",
    r"Celery[/\s].*금지",
    r"Celery\s+전면\s+폐지",
    r"no\s+celery",
    r"check_no_celery",
    r"#.*[Cc]elery",
]


def check_line(line: str, path: str) -> list[str]:
    errors = []
    stripped = line.strip()
    if stripped.startswith("#") or stripped.startswith("//"):
        return errors
    lower = line.lower()
    if "celery" in lower and not any(re.search(p, line, re.I) for p in ALLOWED_PATTERNS):
        errors.append(f"{path}: contains 'celery'")
    return errors


def main():
    errors = []
    for root, dirs, files in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in EXCLUDES and not d.startswith(".")]
        for f in files:
            if f.endswith(".pyc") or f == "check_no_celery.py":
                continue
            path = Path(root) / f
            rel = path.relative_to(ROOT)
            rel_s = str(rel).replace("\\", "/")
            if any(rel_s.startswith(ex) or ex in rel_s.split("/") for ex in EXCLUDES):
                continue
            if rel.suffix not in (".py", ".txt", ".yml", ".yaml", ".toml", ".cfg", ".env.example"):
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            for i, line in enumerate(text.splitlines(), 1):
                for e in check_line(line, f"{rel}:{i}"):
                    if e not in errors:
                        errors.append(e)
    if errors:
        print("ERROR: celery references found:")
        for e in errors[:50]:
            print(f"  {e}")
        if len(errors) > 50:
            print(f"  ... and {len(errors) - 50} more")
        sys.exit(1)
    print("OK: No celery references")
    sys.exit(0)


if __name__ == "__main__":
    main()
