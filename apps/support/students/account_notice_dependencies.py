"""Cross-domain dependencies for deferred student account notices."""

from __future__ import annotations

from typing import Any


def send_welcome_messages(**kwargs: Any) -> Any:
    from apps.domains.messaging.services import send_welcome_messages as _send

    return _send(**kwargs)
