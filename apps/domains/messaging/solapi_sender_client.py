"""Compatibility facade for Solapi sender-number API helpers.

Implementation lives in academy.adapters.messaging.solapi_sender_client.
"""

from __future__ import annotations

from academy.adapters.messaging.solapi_sender_client import (
    get_active_sender_numbers,
    verify_sender_number,
)

__all__ = [
    "get_active_sender_numbers",
    "verify_sender_number",
]
