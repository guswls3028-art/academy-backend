"""Public cross-domain entry points owned by the AI domain."""

from __future__ import annotations

from typing import Any


def dispatch_job(*args: Any, **kwargs: Any):
    """Dispatch an AI job without exposing the gateway implementation module."""
    from .gateway import dispatch_job as _dispatch_job

    return _dispatch_job(*args, **kwargs)


def consume_ai_quota(*args: Any, **kwargs: Any) -> None:
    """Consume AI quota through the domain's public contract boundary."""
    from .services.quota import consume_ai_quota as _consume_ai_quota

    _consume_ai_quota(*args, **kwargs)
