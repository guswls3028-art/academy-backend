"""AI gateway dependencies for staff operations."""

from __future__ import annotations

from typing import Any


def dispatch_staffs_ai_job(**kwargs: Any) -> Any:
    from apps.domains.ai.gateway import dispatch_job

    return dispatch_job(**kwargs)

