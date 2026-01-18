from rest_framework.routers import DefaultRouter
from .views import EnrollmentViewSet, SessionEnrollmentViewSet

router = DefaultRouter()

# ==================================================
# SessionEnrollment (⚠️ 반드시 먼저!)
# ==================================================
router.register(
    r"session-enrollments",
    SessionEnrollmentViewSet,
    basename="session-enrollment",
)

# ==================================================
# Enrollment (⚠️ r"" 는 항상 맨 마지막)
# ==================================================
router.register(
    r"",
    EnrollmentViewSet,
    basename="enrollment",
)

urlpatterns = router.urls
