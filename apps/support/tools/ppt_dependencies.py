"""Cross-domain dependencies for PPT generation views."""

from __future__ import annotations

from typing import Any


def dispatch_ppt_generation_job(**kwargs: Any) -> Any:
    from apps.domains.ai.gateway import dispatch_job

    return dispatch_job(**kwargs)
