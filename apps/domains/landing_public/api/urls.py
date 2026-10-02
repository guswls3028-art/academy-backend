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


from .views.resource_views import PublicResourcePostViewSet, PublicResourceUploadView, PublicResourceFileView

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
    path("uploads/resource/", PublicResourceUploadView.as_view(), name="landing-public-resource-upload"),
    path("resource-files/<uuid:file_id>/", PublicResourceFileView.as_view(), name="landing-public-resource-file"),
    path("", include(router.urls)),
    path("stats/", PublicCommunityStatsView.as_view(), name="landing-public-stats"),
    path("blocks/", PublicUserBlockView.as_view(), name="landing-public-blocks"),
    path("uploads/review-photo/", ReviewPhotoUploadView.as_view(), name="landing-public-review-photo-upload"),
]
