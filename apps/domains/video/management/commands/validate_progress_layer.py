"""
Validate progress layer: no DB in progress endpoint, Redis TTL, job_complete/job_mark_dead delete progress key, no fallback-to-DB.
Exit non-zero if any violation found.
"""

from __future__ import annotations

import os
import sys
from django.core.management.base import BaseCommand


# Progress endpoint module — DO NOT ADD DB ACCESS HERE
PROGRESS_VIEWS_PATH = "apps/support/video/views/progress_views.py"
REDIS_PROGRESS_ADAPTER_PATH = "src/infrastructure/cache/redis_progress_adapter.py"
REDIS_STATUS_CACHE_PATH = "apps/support/video/redis_status_cache.py"
REPOSITORIES_VIDEO_PATH = "academy/adapters/db/django/repositories_video.py"

# Patterns that must NOT appear in progress endpoint (VideoProgressView.get path)
DB_PATTERNS = [
    "Video.objects",
    "VideoTranscodeJob",
    "get_video_for_update",
    "session__lecture__tenant",
]

# Comment that must appear in progress views
REQUIRED_COMMENT = "DO NOT ADD DB ACCESS HERE (PROGRESS ENDPOINT)"


class Command(BaseCommand):
    help = "Validate progress layer: no DB in progress endpoint, Redis TTL, delete on complete/dead, no DB fallback"

    def handle(self, *args, **options):
        repo_root = self._find_repo_root()
        errors = []

        # 1) No DB query inside progress endpoint
        progress_views_full = os.path.join(repo_root, PROGRESS_VIEWS_PATH.replace("/", os.sep))
        if os.path.isfile(progress_views_full):
            with open(progress_views_full, "r", encoding="utf-8") as f:
                content = f.read()
            if REQUIRED_COMMENT not in content:
                errors.append(f"Missing assertion comment '{REQUIRED_COMMENT}' in {PROGRESS_VIEWS_PATH}")
            view_section = self._extract_progress_view_get_section(content)
            if view_section:
                for pat in DB_PATTERNS:
                    if pat in view_section:
                        errors.append(f"Progress endpoint must not use DB pattern '{pat}' in {PROGRESS_VIEWS_PATH}")
        else:
            errors.append(f"Progress views file not found: {progress_views_full}")

        # 2) Redis progress keys have TTL (setex in adapter)
        adapter_path = os.path.join(repo_root, REDIS_PROGRESS_ADAPTER_PATH.replace("/", os.sep))
        if os.path.isfile(adapter_path):
            with open(adapter_path, "r", encoding="utf-8") as f:
                adapter_content = f.read()
            if "setex" not in adapter_content:
                errors.append(f"Redis progress adapter must use setex for TTL: {REDIS_PROGRESS_ADAPTER_PATH}")
            if "PROGRESS_TTL_SECONDS" not in adapter_content:
                errors.append(f"Redis progress adapter must define PROGRESS_TTL_SECONDS: {REDIS_PROGRESS_ADAPTER_PATH}")
            if "86400" not in adapter_content:
                errors.append(f"Progress TTL default should be 24h (86400) in {REDIS_PROGRESS_ADAPTER_PATH}")
        else:
            errors.append(f"Redis progress adapter not found: {adapter_path}")

        # 3) delete_video_progress_key exists
        cache_path = os.path.join(repo_root, REDIS_STATUS_CACHE_PATH.replace("/", os.sep))
        if os.path.isfile(cache_path):
            with open(cache_path, "r", encoding="utf-8") as f:
                cache_content = f.read()
            if "delete_video_progress_key" not in cache_content:
                errors.append(f"redis_status_cache must define delete_video_progress_key: {REDIS_STATUS_CACHE_PATH}")
        else:
            errors.append(f"Redis status cache not found: {cache_path}")

        # 4) job_complete and job_mark_dead delete progress key
        repo_path = os.path.join(repo_root, REPOSITORIES_VIDEO_PATH.replace("/", os.sep))
        if os.path.isfile(repo_path):
            with open(repo_path, "r", encoding="utf-8") as f:
                repo_content = f.read()
            job_complete_section = self._extract_function_section(repo_content, "def job_complete(")
            if not job_complete_section:
                errors.append(f"job_complete not found in {REPOSITORIES_VIDEO_PATH}")
            elif "delete_video_progress_key" not in job_complete_section:
                errors.append(f"job_complete must call delete_video_progress_key in {REPOSITORIES_VIDEO_PATH}")

            job_mark_dead_section = self._extract_function_section(repo_content, "def job_mark_dead(")
            if not job_mark_dead_section:
                errors.append(f"job_mark_dead not found in {REPOSITORIES_VIDEO_PATH}")
            elif "delete_video_progress_key" not in job_mark_dead_section:
                errors.append(f"job_mark_dead must call delete_video_progress_key in {REPOSITORIES_VIDEO_PATH}")
        else:
            errors.append(f"Repositories video not found: {repo_path}")

        # 5) On Redis miss return UNKNOWN without DB
        if os.path.isfile(progress_views_full):
            with open(progress_views_full, "r", encoding="utf-8") as f:
                content = f.read()
            if "cached_status is None" in content and "UNKNOWN" not in content:
                errors.append(f"On Redis miss progress endpoint must return state UNKNOWN: {PROGRESS_VIEWS_PATH}")

        if errors:
            for e in errors:
                self.stdout.write(self.style.ERROR(e))
            sys.exit(1)

        self.stdout.write(self.style.SUCCESS("validate_progress_layer: OK"))

    def _extract_progress_view_get_section(self, content: str) -> str | None:
        """Extract VideoProgressView get() method body."""
        lines = content.splitlines()
        start = None
        for i, line in enumerate(lines):
            if "class VideoProgressView(" in line:
                start = i
                break
        if start is None:
            return None
        # Include from start of class to next top-level class or end of file
        end = len(lines)
        indent_class = None
        for i in range(start + 1, len(lines)):
            line = lines[i]
            if line.strip() and not line.strip().startswith("#"):
                if indent_class is None and "def " in line:
                    indent_class = len(line) - len(line.lstrip())
                if indent_class is not None and line.strip().startswith("class ") and len(line) - len(line.lstrip()) <= indent_class:
                    end = i
                    break
        return "\n".join(lines[start:end])

    def _extract_function_section(self, content: str, func_def: str) -> str | None:
        """Extract a function body by finding func_def."""
        lines = content.splitlines()
        start = None
        for i, line in enumerate(lines):
            if func_def in line and line.strip().startswith("def "):
                start = i
                break
        if start is None:
            return None
        return "\n".join(lines[start: start + 85])

    def _find_repo_root(self):
        cur = os.path.dirname(os.path.abspath(__file__))
        for _ in range(12):
            if os.path.isdir(os.path.join(cur, "apps", "support", "video")):
                return cur
            parent = os.path.dirname(cur)
            if parent == cur:
                break
            cur = parent
        return cur
