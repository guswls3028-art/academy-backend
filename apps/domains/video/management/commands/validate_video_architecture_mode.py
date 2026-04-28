"""
Ensure no Lambda is used for video: no Lambda definitions, no Lambda IAM refs, EventBridge targets are Batch only.
Exits non-zero if Lambda artifacts found.
"""

from __future__ import annotations

import os
import sys
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Validate video architecture: Batch/ECS only, no Lambda"

    def handle(self, *args, **options):
        repo_root = self._find_repo_root()
        errors = []

        # No lambda_function.py for video batch (reconcile, scan_stuck)
        for dirpath, _dirnames, filenames in os.walk(repo_root):
            if "lambda_function.py" in filenames:
                rel = os.path.relpath(dirpath, repo_root)
                if "video_reconcile_lambda" in rel or "video_scan_stuck_lambda" in rel:
                    errors.append(f"Lambda source found: {os.path.join(rel, 'lambda_function.py')}")

        # No Lambda references in deploy scripts
        for dirpath, _dirnames, filenames in os.walk(repo_root):
            if "scripts" not in dirpath and "infra" not in dirpath:
                continue
            for f in filenames:
                if not f.endswith((".ps1", ".json", ".py")):
                    continue
                path = os.path.join(dirpath, f)
                try:
                    with open(path, "r", encoding="utf-8", errors="ignore") as fp:
                        text = fp.read()
                except Exception:
                    continue
                if "lambda:AddPermission" in text or "aws lambda " in text.lower():
                    if "video" in path.lower() or "eventbridge_deploy_video" in path:
                        errors.append(f"Lambda reference in script: {path}")
                if "video_reconcile_lambda" in text or "video_scan_stuck_lambda" in text:
                    errors.append(f"Lambda path reference: {path}")

        # EventBridge target JSONs must point to Batch (Arn contains batch or job-queue)
        eventbridge_path = os.path.join(repo_root, "scripts", "infra", "eventbridge")
        if os.path.isdir(eventbridge_path):
            for name in os.listdir(eventbridge_path):
                if not name.endswith(".json"):
                    continue
                path = os.path.join(eventbridge_path, name)
                try:
                    with open(path, "r", encoding="utf-8") as fp:
                        text = fp.read()
                except Exception:
                    continue
                if "lambda" in text.lower() and "batch" not in text.lower():
                    errors.append(f"EventBridge target may reference Lambda: {path}")
                if "JobDefinition" not in text and "BatchParameters" not in text and "batch" in name.lower():
                    errors.append(f"EventBridge video target missing BatchParameters: {path}")

        if errors:
            for e in errors:
                self.stdout.write(self.style.ERROR(e))
            sys.exit(1)

        self.stdout.write(self.style.SUCCESS("validate_video_architecture_mode: OK (no Lambda artifacts)"))

    def _find_repo_root(self):
        cur = os.path.dirname(os.path.abspath(__file__))
        for _ in range(10):
            if os.path.isdir(os.path.join(cur, "scripts", "infra")):
                return cur
            parent = os.path.dirname(cur)
            if parent == cur:
                break
            cur = parent
        return cur
