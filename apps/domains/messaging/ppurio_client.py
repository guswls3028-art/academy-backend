"""Compatibility wrapper for Ppurio messaging support client."""

from __future__ import annotations

from apps.support.messaging.ppurio_client import (
    DEFAULT_API_URL,
    _get_access_token,
    send_ppurio_alimtalk,
    send_ppurio_sms,
)

__all__ = [
    "DEFAULT_API_URL",
    "_get_access_token",
    "send_ppurio_alimtalk",
    "send_ppurio_sms",
]
