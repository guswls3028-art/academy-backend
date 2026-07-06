"""Cross-domain dependencies for student import workflows."""

from __future__ import annotations

from typing import Any


def send_welcome_messages(**kwargs: Any) -> Any:
    from apps.domains.messaging.services import send_welcome_messages as _send

    return _send(**kwargs)


def get_tenant_site_url(tenant: Any) -> str:
    from apps.domains.messaging.services import get_tenant_site_url as _get_url

    return _get_url(tenant)
