# apps/support/messaging/urls.py
from django.urls import path
from apps.domains.messaging import views
from apps.domains.messaging import views_notification

urlpatterns = [
    path("info/", views.MessagingInfoView.as_view(), name="messaging-info"),
    path("log/", views.NotificationLogListView.as_view(), name="messaging-log"),
    path("log/<int:pk>/", views.NotificationLogDetailView.as_view(), name="messaging-log-detail"),
    path("scheduled/", views.ScheduledNotificationListView.as_view(), name="messaging-scheduled"),
    path(
        "scheduled/<int:pk>/cancel/",
        views.ScheduledNotificationCancelView.as_view(),
        name="messaging-scheduled-cancel",
    ),
    path("channel-check/", views.ChannelCheckView.as_view(), name="messaging-channel-check"),
    path("operations/status/", views.MessagingOperationsStatusView.as_view(), name="messaging-operations-status"),
    path("send/preflight/", views.SendMessagePreflightView.as_view(), name="messaging-send-preflight"),
    path("send/", views.SendMessageView.as_view(), name="messaging-send"),
    path("templates/", views.MessageTemplateListCreateView.as_view(), name="messaging-templates"),
    path("templates/<int:pk>/", views.MessageTemplateDetailView.as_view(), name="messaging-template-detail"),
    path(
        "templates/<int:pk>/submit-review/",
        views.MessageTemplateSubmitReviewView.as_view(),
        name="messaging-template-submit-review",
    ),
    path(
        "templates/<int:pk>/set-default/",
        views.MessageTemplateSetDefaultView.as_view(),
        name="messaging-template-set-default",
    ),
    path(
        "templates/<int:pk>/duplicate/",
        views.MessageTemplateDuplicateView.as_view(),
        name="messaging-template-duplicate",
    ),
    path(
        "templates/sync-solapi/",
        views.SolapiSyncTemplatesView.as_view(),
        name="messaging-template-sync-solapi",
    ),
    path("auto-send/", views.AutoSendConfigView.as_view(), name="messaging-auto-send"),
    path("provision-defaults/", views.ProvisionDefaultTemplatesView.as_view(), name="messaging-provision-defaults"),
    path("test-credentials/", views.TestCredentialsView.as_view(), name="messaging-test-credentials"),
    # 수동 알림 발송 (preview → confirm)
    path(
        "attendance-notification/preview/",
        views_notification.AttendanceNotificationPreviewView.as_view(),
        name="attendance-notification-preview",
    ),
    path(
        "attendance-notification/confirm/",
        views_notification.AttendanceNotificationConfirmView.as_view(),
        name="attendance-notification-confirm",
    ),
    # 범용 수동 알림 발송 (시험/과제/퇴원 등)
    path(
        "manual-notification/preview/",
        views_notification.ManualNotificationPreviewView.as_view(),
        name="manual-notification-preview",
    ),
    path(
        "manual-notification/confirm/",
        views_notification.ManualNotificationConfirmView.as_view(),
        name="manual-notification-confirm",
    ),
]
