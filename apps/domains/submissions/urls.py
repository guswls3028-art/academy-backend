from rest_framework.routers import DefaultRouter
from .views import SubmissionViewSet

router = DefaultRouter()
router.register("submissions", SubmissionViewSet, basename="submissions")

urlpatterns = router.urls
