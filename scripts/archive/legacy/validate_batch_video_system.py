#!/usr/bin/env python3
"""
AWS Batch video transcoding system health check.
Run from repo root: python scripts/validate_batch_video_system.py
  or: python manage.py validate_batch_video_system
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main():
    r = subprocess.run(
        [sys.executable, "manage.py", "validate_batch_video_system"],
        cwd=ROOT,
        timeout=120,
    )
    return r.returncode


if __name__ == "__main__":
    sys.exit(main())
