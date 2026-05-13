from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import (
    PublicBoardPostViewSet,
    PublicCommunityStatsView,
    PublicExamShowcaseViewSet,
    PublicMatchupShowcaseViewSet,
    PublicPostReplyViewSet,
    PublicReportViewSet,
    PublicReviewViewSet,
    PublicUserBlockView,
    ReviewPhotoUploadView,
)


router = DefaultRouter()
router.register("board", PublicBoardPostViewSet, basename="landing-public-board")
router.register("reviews", PublicReviewViewSet, basename="landing-public-review")
router.register("replies", PublicPostReplyViewSet, basename="landing-public-reply")
router.register("reports", PublicReportViewSet, basename="landing-public-report")
router.register("showcase", PublicExamShowcaseViewSet, basename="landing-public-showcase")
router.register("matchup-showcase", PublicMatchupShowcaseViewSet, basename="landing-public-matchup-showcase")

urlpatterns = [
    path("", include(router.urls)),
    path("stats/", PublicCommunityStatsView.as_view(), name="landing-public-stats"),
    path("blocks/", PublicUserBlockView.as_view(), name="landing-public-blocks"),
    path("uploads/review-photo/", ReviewPhotoUploadView.as_view(), name="landing-public-review-photo-upload"),
]
