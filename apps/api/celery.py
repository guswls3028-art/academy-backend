# PATH: apps/api/celery.py
"""
Project intentionally runs with **no task queue**.

This module exists only because some deployments/tools may still import it.
Do not add any third-party queue/task dependencies here.
"""
from __future__ import annotations

# NOTE:
# - Keep this file import-safe.
# - Do not import task queue libraries.
# - If something imports `apps.api.celery`, it should not crash.

__all__ = ["get_app"]


def get_app():
    """
    Compatibility hook. Returns None because the project is queue-less.
    """
    return None
