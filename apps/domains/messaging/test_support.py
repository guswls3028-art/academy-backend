"""Public fixtures for domains verifying durable messaging admission."""

from apps.domains.messaging.models import ScheduledNotification
from apps.domains.messaging.services.registration_service import _dispatch_registration_durably


def create_scheduled_notification_fixture(**kwargs):
    return ScheduledNotification.objects.create(**kwargs)


def scheduled_notification_fixtures(**kwargs):
    return ScheduledNotification.objects.filter(**kwargs)


def admit_registration_notification_fixture(**kwargs):
    """Exercise real durable admission with caller-provided synthetic payloads."""
    return _dispatch_registration_durably(**kwargs)
