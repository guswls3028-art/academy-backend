# apps/support/messaging/services/__init__.py
"""
Re-export all public symbols for backward compatibility.
`enqueue_sms` and `send_sms` are historical public API names. The former
accepts only Alimtalk mode and the latter always fails closed with
`sms_disabled`.
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
from .recipients import (
    StudentMessageRecipient,
    normalize_phone,
    resolve_student_message_recipients,
)
from .notification_service import (
    send_event_notification,
    send_clinic_reminder_for_students,
    send_due_clinic_reminders,
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
    # recipients
    "StudentMessageRecipient",
    "normalize_phone",
    "resolve_student_message_recipients",
    # notification_service
    "send_event_notification",
    "send_clinic_reminder_for_students",
    "send_due_clinic_reminders",
    # registration_service
    "REGISTRATION_APPROVED_NOTICE",
    "send_welcome_messages",
    "send_registration_approved_messages",
]
