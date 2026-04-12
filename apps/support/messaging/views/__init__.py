# apps/support/messaging/views/__init__.py
"""
Re-export all view classes for backward compatibility.
Usage: ``from apps.support.messaging import views`` or
       ``from apps.support.messaging.views import MessagingInfoView``
"""

from .info_views import (
    MessagingInfoView,
    ChargeView,
    VerifySenderView,
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
)
from .send_views import (
    SendMessageView,
)
from .config_views import (
    AutoSendConfigView,
    ProvisionDefaultTemplatesView,
)

__all__ = [
    "MessagingInfoView",
    "ChargeView",
    "VerifySenderView",
    "ChannelCheckView",
    "TestCredentialsView",
    "NotificationLogListView",
    "NotificationLogDetailView",
    "MessageTemplateListCreateView",
    "MessageTemplateDetailView",
    "MessageTemplateSetDefaultView",
    "MessageTemplateDuplicateView",
    "MessageTemplateSubmitReviewView",
    "SendMessageView",
    "AutoSendConfigView",
    "ProvisionDefaultTemplatesView",
]
