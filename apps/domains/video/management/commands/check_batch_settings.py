# PATH: apps/support/video/management/commands/check_batch_settings.py
"""
Verify AWS Batch Video Job runtime settings.

Usage:
  python manage.py check_batch_settings

Exit 0 if VIDEO_BATCH_JOB_QUEUE and VIDEO_BATCH_JOB_DEFINITION are set.
Exit 1 if either is missing.
"""
from django.core.management.base import BaseCommand
from django.conf import settings


class Command(BaseCommand):
    help = "Verify AWS Batch Video Job runtime settings"

    def handle(self, *args, **kwargs):
        required = [
            "VIDEO_BATCH_JOB_QUEUE",
            "VIDEO_BATCH_JOB_DEFINITION",
        ]

        for key in required:
            val = getattr(settings, key, None)
            if not val:
                self.stderr.write(f"FAIL: Missing {key}")
                exit(1)
            else:
                self.stdout.write(f"OK: {key}={val}")

        self.stdout.write("PASS")
