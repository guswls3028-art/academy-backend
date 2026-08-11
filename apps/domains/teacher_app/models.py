"""Django model-discovery entry point for the teacher app."""

from .push.models import PushNotificationConfig, PushSubscription


__all__ = ["PushNotificationConfig", "PushSubscription"]
