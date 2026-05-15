# apps/support/messaging/services/__init__.py
"""
Re-export all public symbols for backward compatibility.
`enqueue_sms` is the historical public API name; it enqueues either SMS or
Alimtalk depending on `message_mode`.
"""

from .solapi_client import (
    _get_solapi_credentials,
    _is_mock_mode,
    get_solapi_client,
    send_sms,
)
from .queue_service import (
    enqueue_sms,
    is_reservation_cancelled,
)
from .url_helpers import (
    get_site_url,
    get_tenant_site_url,
)
from .notification_service import (
    send_event_notification,
    send_clinic_reminder_for_students,
)
from .registration_service import (
    REGISTRATION_APPROVED_NOTICE,
    send_welcome_messages,
    send_registration_approved_messages,
)

__all__ = [
    # solapi_client
    "_get_solapi_credentials",
    "_is_mock_mode",
    "get_solapi_client",
    "send_sms",
    # queue_service
    "enqueue_sms",
    "is_reservation_cancelled",
    # url_helpers
    "get_site_url",
    "get_tenant_site_url",
    # notification_service
    "send_event_notification",
    "send_clinic_reminder_for_students",
    # registration_service
    "REGISTRATION_APPROVED_NOTICE",
    "send_welcome_messages",
    "send_registration_approved_messages",
]
