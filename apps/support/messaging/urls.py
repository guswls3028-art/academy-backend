# apps/support/messaging/urls.py
from django.urls import path
from apps.support.messaging import views

urlpatterns = [
    path("info/", views.MessagingInfoView.as_view(), name="messaging-info"),
    path("verify-sender/", views.VerifySenderView.as_view(), name="messaging-verify-sender"),
    path("charge/", views.ChargeView.as_view(), name="messaging-charge"),
    path("log/", views.NotificationLogListView.as_view(), name="messaging-log"),
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
]
