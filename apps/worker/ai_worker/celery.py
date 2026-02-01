# PATH: apps/worker/ai_worker/celery.py
"""
This project is queue-less.

Kept only to avoid breaking imports in legacy scripts.
Do not add any queue/task dependencies here.
"""
from __future__ import annotations

__all__ = ["get_app"]


def get_app():
    return None
