#!/usr/bin/env python
"""Django's command-line utility for administrative tasks."""
# manage.py (상단)
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

# .env (배포용) 먼저, .env.local (로컬) 이 있으면 덮어씀
BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")
load_dotenv(BASE_DIR / ".env.local")


def main():
    """Run administrative tasks."""
    if str(BASE_DIR) not in sys.path:
        sys.path.insert(0, str(BASE_DIR))

    dsm = os.environ.get("DJANGO_SETTINGS_MODULE", "").strip()
    if not dsm:
        print(
            "FAIL: DJANGO_SETTINGS_MODULE is not set. Set it in .env or ensure Batch/worker runs via entrypoint (SSM JSON).",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Couldn't import Django. Are you sure it's installed and "
            "available on your PYTHONPATH environment variable? Did you "
            "forget to activate a virtual environment?"
        ) from exc

    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
