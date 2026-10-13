from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import (
    PublicBoardPostViewSet,
    PublicCommunityStatsView,
    PublicExamShowcaseViewSet,
    PublicMatchupShowcaseViewSet,
    PublicProblemReviewShowcaseViewSet,
    PublicPostReplyViewSet,
    PublicReportViewSet,
    PublicReviewViewSet,
    PublicUserBlockView,
    ReviewPhotoUploadView,
)


from .views.resource_views import PublicResourcePostViewSet, PublicResourceUploadView, PublicResourceFileView, PublicResourceReaderView
from .views.video_views import (
    PublicVideoListView, PublicVideoDetailView, PublicVideoShowcaseView, PublicVideoCapabilityView,
    PublicVideoResolveView, ManageVideoListView, ManageVideoDetailView, ManageVideoPublishView,
    ManageVideoUnpublishView, ManageVideoShowcaseView,
)

router = DefaultRouter()
router.register("resources", PublicResourcePostViewSet, basename="landing-public-resource")
router.register("board", PublicBoardPostViewSet, basename="landing-public-board")
router.register("reviews", PublicReviewViewSet, basename="landing-public-review")
router.register("replies", PublicPostReplyViewSet, basename="landing-public-reply")
router.register("reports", PublicReportViewSet, basename="landing-public-report")
router.register("showcase", PublicExamShowcaseViewSet, basename="landing-public-showcase")
router.register("matchup-showcase", PublicMatchupShowcaseViewSet, basename="landing-public-matchup-showcase")
router.register(
    "problem-review-showcase",
    PublicProblemReviewShowcaseViewSet,
    basename="landing-public-problem-review-showcase",
)

urlpatterns = [
    path("videos/", PublicVideoListView.as_view(), name="public-video-list"),
    path("videos/showcase/", PublicVideoShowcaseView.as_view(), name="public-video-showcase"),
    path("videos/capabilities/", PublicVideoCapabilityView.as_view(), name="public-video-capabilities"),
    path("videos/resolve/", PublicVideoResolveView.as_view(), name="public-video-resolve"),
    path("videos/manage/", ManageVideoListView.as_view(), name="manage-video-list"),
    path("videos/manage/showcase/", ManageVideoShowcaseView.as_view(), name="manage-video-showcase"),
    path("videos/manage/<uuid:pk>/", ManageVideoDetailView.as_view(), name="manage-video-detail"),
    path("videos/manage/<uuid:pk>/publish/", ManageVideoPublishView.as_view(), name="manage-video-publish"),
    path("videos/manage/<uuid:pk>/unpublish/", ManageVideoUnpublishView.as_view(), name="manage-video-unpublish"),
    path("videos/<uuid:pk>/", PublicVideoDetailView.as_view(), name="public-video-detail"),
    path("uploads/resource/", PublicResourceUploadView.as_view(), name="landing-public-resource-upload"),
    path("resource-files/<uuid:file_id>/reader/", PublicResourceReaderView.as_view(), name="landing-public-resource-reader"),
    path("resource-files/<uuid:file_id>/", PublicResourceFileView.as_view(), name="landing-public-resource-file"),
    path("", include(router.urls)),
    path("stats/", PublicCommunityStatsView.as_view(), name="landing-public-stats"),
    path("blocks/", PublicUserBlockView.as_view(), name="landing-public-blocks"),
    path("uploads/review-photo/", ReviewPhotoUploadView.as_view(), name="landing-public-review-photo-upload"),
]
