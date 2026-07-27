from django.urls import path

from .push.views import (
    PushNotificationConfigView,
    PushSubscribeView,
    PushUnsubscribeView,
    PlatformPushSubscribeView,
    PlatformPushUnsubscribeView,
    PlatformVapidPublicKeyView,
    VapidPublicKeyView,
)
from .views import NotificationSummaryView

urlpatterns = [
    # BFF
    path("notifications/summary/", NotificationSummaryView.as_view(), name="teacher-notification-summary"),

    # Push
    path("push/subscribe/", PushSubscribeView.as_view(), name="teacher-push-subscribe"),
    path("push/unsubscribe/", PushUnsubscribeView.as_view(), name="teacher-push-unsubscribe"),
    path("push/vapid-key/", VapidPublicKeyView.as_view(), name="teacher-push-vapid-key"),
    path("push/config/", PushNotificationConfigView.as_view(), name="teacher-push-config"),
    path(
        "push/platform/subscribe/",
        PlatformPushSubscribeView.as_view(),
        name="platform-push-subscribe",
    ),
    path(
        "push/platform/unsubscribe/",
        PlatformPushUnsubscribeView.as_view(),
        name="platform-push-unsubscribe",
    ),
    path(
        "push/platform/vapid-key/",
        PlatformVapidPublicKeyView.as_view(),
        name="platform-push-vapid-key",
    ),
]
