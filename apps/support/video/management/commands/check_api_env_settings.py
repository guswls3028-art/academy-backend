# PATH: apps/support/video/management/commands/check_api_env_settings.py
"""
Verify API runtime environment variables (DB, Redis, R2, Batch, AWS).

Usage:
  python manage.py check_api_env_settings
  python manage.py check_api_env_settings --verbose

Exit 0 if all required vars are set. Exit 1 if any missing.
Secrets are masked (show first 2 chars + ***) unless --verbose.
"""
from __future__ import annotations

import os

from django.core.management.base import BaseCommand


SECRET_KEYS = frozenset({"DB_PASSWORD", "R2_ACCESS_KEY", "R2_SECRET_KEY", "INTERNAL_WORKER_TOKEN"})


def _mask(val: str | None, key: str, show_full: bool = False) -> str:
    if val is None or val == "":
        return "(empty)"
    if show_full or key not in SECRET_KEYS:
        return val
    return f"{val[:2]}***"


# required: missing -> exit 1
REQUIRED = [
    "DB_HOST",
    "DB_NAME",
    "DB_USER",
    "DB_PASSWORD",
    "REDIS_HOST",
    "R2_ACCESS_KEY",
    "R2_SECRET_KEY",
    "R2_ENDPOINT",
    "AWS_REGION",
    "VIDEO_BATCH_JOB_QUEUE",
    "VIDEO_BATCH_JOB_DEFINITION",
]

# optional: warn if missing
OPTIONAL = [
    "R2_VIDEO_BUCKET",
    "R2_PUBLIC_BASE_URL",
    "CDN_HLS_BASE_URL",
    "INTERNAL_WORKER_TOKEN",
]


class Command(BaseCommand):
    help = "Verify API runtime environment variables (DB, Redis, R2, Batch, AWS)"

    def add_arguments(self, parser):
        parser.add_argument(
            "--verbose",
            action="store_true",
            help="Show full values for secrets (dev only)",
        )

    def handle(self, *args, **options):
        verbose = options.get("verbose", False)
        missing = []
        ok = []

        for key in REQUIRED:
            val = os.environ.get(key)
            if val is None or (isinstance(val, str) and val.strip() == ""):
                missing.append(key)
            else:
                ok.append((key, val))

        if missing:
            self.stderr.write(self.style.ERROR(f"FAIL: Missing required: {', '.join(missing)}"))
            self.stderr.write("  Set in .env / SSM / docker env-file and redeploy.")
            exit(1)

        self.stdout.write("Required env (OK):")
        for key, val in ok:
            masked = _mask(val, show_full=verbose or key in ("DB_HOST", "DB_NAME", "REDIS_HOST", "VIDEO_BATCH_JOB_QUEUE", "VIDEO_BATCH_JOB_DEFINITION"))
            self.stdout.write(f"  {key}={masked}")

        warned = []
        for key in OPTIONAL:
            val = os.environ.get(key)
            if val is None or (isinstance(val, str) and val.strip() == ""):
                warned.append(key)
            else:
                masked = _mask(val, show_full=verbose or "BUCKET" in key or "URL" in key)
                self.stdout.write(f"  {key}={masked} (OK)")

        if warned:
            self.stdout.write(self.style.WARNING(f"WARN: Optional missing: {', '.join(warned)}"))

        self.stdout.write(self.style.SUCCESS("PASS"))
