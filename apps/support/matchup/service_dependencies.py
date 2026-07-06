"""Cross-domain dependency helpers for matchup services."""

from __future__ import annotations

from typing import Any


def dispatch_ai_job(**kwargs: Any) -> Any:
    from apps.domains.ai.gateway import dispatch_job

    return dispatch_job(**kwargs)
