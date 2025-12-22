from rest_framework.routers import DefaultRouter
from .views import EnrollmentViewSet, SessionEnrollmentViewSet

router = DefaultRouter()

router.register(
    r"",
    EnrollmentViewSet,
    basename="enrollment",
)

router.register(
    r"session-enrollments",
    SessionEnrollmentViewSet,
    basename="session-enrollment",
)

urlpatterns = router.urls
