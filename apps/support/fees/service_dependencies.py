"""Cross-domain service dependencies for fees."""

from __future__ import annotations

from typing import Any


def send_event_notification(**kwargs: Any) -> Any:
    from apps.domains.messaging.services import send_event_notification as _send

    return _send(**kwargs)

