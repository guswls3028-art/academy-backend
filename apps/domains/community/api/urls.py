from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    PostViewSet, AdminPostViewSet, ScopeNodeViewSet, PostTemplateViewSet,
    PlatformInboxListView, PlatformInboxReplyView, PlatformInboxDeleteReplyView,
    PlatformInboxAttachmentDownloadView,
)

router = DefaultRouter()
router.register("posts", PostViewSet, basename="community-post")
router.register("scope-nodes", ScopeNodeViewSet, basename="community-scope-node")
router.register("post-templates", PostTemplateViewSet, basename="community-post-template")

admin_router = DefaultRouter()
admin_router.register("posts", AdminPostViewSet, basename="community-admin-post")

urlpatterns = [
    path("", include(router.urls)),
    path("admin/", include(admin_router.urls)),
    # Platform inbox (superuser only — dev_app)
    path("platform/inbox/", PlatformInboxListView.as_view(), name="platform-inbox-list"),
    path("platform/inbox/<int:post_id>/replies/", PlatformInboxReplyView.as_view(), name="platform-inbox-reply"),
    path("platform/inbox/<int:post_id>/replies/<int:reply_id>/", PlatformInboxDeleteReplyView.as_view(), name="platform-inbox-reply-delete"),
    path("platform/inbox/<int:post_id>/attachments/<int:att_id>/download/", PlatformInboxAttachmentDownloadView.as_view(), name="platform-inbox-attachment-download"),
]
