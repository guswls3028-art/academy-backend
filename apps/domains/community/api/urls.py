from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import PostViewSet, AdminPostViewSet, BlockTypeViewSet, ScopeNodeViewSet, PostTemplateViewSet

router = DefaultRouter()
router.register("posts", PostViewSet, basename="community-post")
router.register("block-types", BlockTypeViewSet, basename="community-block-type")
router.register("scope-nodes", ScopeNodeViewSet, basename="community-scope-node")
router.register("post-templates", PostTemplateViewSet, basename="community-post-template")

admin_router = DefaultRouter()
admin_router.register("posts", AdminPostViewSet, basename="community-admin-post")

urlpatterns = [
    path("", include(router.urls)),
    path("admin/", include(admin_router.urls)),
]
