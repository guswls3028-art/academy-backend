# apps/support/messaging/urls.py
from django.urls import path
from apps.support.messaging import views
from apps.support.messaging import views_notification

urlpatterns = [
    path("info/", views.MessagingInfoView.as_view(), name="messaging-info"),
    path("verify-sender/", views.VerifySenderView.as_view(), name="messaging-verify-sender"),
    path("charge/", views.ChargeView.as_view(), name="messaging-charge"),
    path("log/", views.NotificationLogListView.as_view(), name="messaging-log"),
    path("log/<int:pk>/", views.NotificationLogDetailView.as_view(), name="messaging-log-detail"),
    path("channel-check/", views.ChannelCheckView.as_view(), name="messaging-channel-check"),
    path("send/", views.SendMessageView.as_view(), name="messaging-send"),
    path("templates/", views.MessageTemplateListCreateView.as_view(), name="messaging-templates"),
    path("templates/<int:pk>/", views.MessageTemplateDetailView.as_view(), name="messaging-template-detail"),
    path(
        "templates/<int:pk>/submit-review/",
        views.MessageTemplateSubmitReviewView.as_view(),
        name="messaging-template-submit-review",
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
