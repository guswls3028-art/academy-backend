from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    PostViewSet, AdminPostViewSet, AdminReportsViewSet, CommunityStatsView, CommunityUserBlockView, ScopeNodeViewSet, PostTemplateViewSet,
    PlatformInboxListView, PlatformInboxReplyView, PlatformInboxDeleteReplyView,
    PlatformInboxAttachmentDownloadView,
    CommunityNotificationListView, CommunityNotificationUnreadCountView,
    CommunityNotificationReadView, CommunityNotificationMarkAllReadView,
)

router = DefaultRouter()
router.register("posts", PostViewSet, basename="community-post")
router.register("scope-nodes", ScopeNodeViewSet, basename="community-scope-node")
router.register("post-templates", PostTemplateViewSet, basename="community-post-template")

admin_router = DefaultRouter()
admin_router.register("posts", AdminPostViewSet, basename="community-admin-post")
admin_router.register("reports", AdminReportsViewSet, basename="community-admin-report")

urlpatterns = [
    path("", include(router.urls)),
    path("admin/", include(admin_router.urls)),
    path("admin/stats/", CommunityStatsView.as_view(), name="community-admin-stats"),
    path("admin/user-blocks/", CommunityUserBlockView.as_view(), name="community-admin-user-block-list"),
    path("admin/user-blocks/<int:user_id>/", CommunityUserBlockView.as_view(), name="community-admin-user-block-detail"),
    # Notifications (학생/학부모/staff 본인 알림)
    path("notifications/", CommunityNotificationListView.as_view(), name="community-notification-list"),
    path("notifications/unread-count/", CommunityNotificationUnreadCountView.as_view(), name="community-notification-unread-count"),
    path("notifications/<int:pk>/read/", CommunityNotificationReadView.as_view(), name="community-notification-read"),
    path("notifications/mark-all-read/", CommunityNotificationMarkAllReadView.as_view(), name="community-notification-mark-all-read"),
    # Platform inbox (superuser only — dev_app)
    path("platform/inbox/", PlatformInboxListView.as_view(), name="platform-inbox-list"),
    path("platform/inbox/<int:post_id>/replies/", PlatformInboxReplyView.as_view(), name="platform-inbox-reply"),
    path("platform/inbox/<int:post_id>/replies/<int:reply_id>/", PlatformInboxDeleteReplyView.as_view(), name="platform-inbox-reply-delete"),
    path("platform/inbox/<int:post_id>/attachments/<int:att_id>/download/", PlatformInboxAttachmentDownloadView.as_view(), name="platform-inbox-attachment-download"),
]
