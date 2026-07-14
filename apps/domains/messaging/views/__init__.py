# apps/support/messaging/views/__init__.py
"""
Re-export all view classes for backward compatibility.
Usage: ``from apps.domains.messaging import views`` or
       ``from apps.domains.messaging.views import MessagingInfoView``
"""

from .info_views import (
    MessagingInfoView,
    ChannelCheckView,
    TestCredentialsView,
)
from .log_views import (
    NotificationLogListView,
    NotificationLogDetailView,
)
from .template_views import (
    MessageTemplateListCreateView,
    MessageTemplateDetailView,
    MessageTemplateSetDefaultView,
    MessageTemplateDuplicateView,
    MessageTemplateSubmitReviewView,
    SolapiSyncTemplatesView,
)
from .send_views import (
    SendMessageView,
)
from .config_views import (
    AutoSendConfigView,
    ProvisionDefaultTemplatesView,
)
from .scheduled_views import (
    ScheduledNotificationCancelView,
    ScheduledNotificationListView,
)
from .operations_views import (
    MessagingOperationsStatusView,
    SendMessagePreflightView,
)

__all__ = [
    "MessagingInfoView",
    "ChannelCheckView",
    "TestCredentialsView",
    "NotificationLogListView",
    "NotificationLogDetailView",
    "MessageTemplateListCreateView",
    "MessageTemplateDetailView",
    "MessageTemplateSetDefaultView",
    "MessageTemplateDuplicateView",
    "MessageTemplateSubmitReviewView",
    "SolapiSyncTemplatesView",
    "SendMessageView",
    "AutoSendConfigView",
    "ProvisionDefaultTemplatesView",
    "ScheduledNotificationCancelView",
    "ScheduledNotificationListView",
    "SendMessagePreflightView",
    "MessagingOperationsStatusView",
]
