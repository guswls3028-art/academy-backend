# apps/domains/attendance/urls.py

from rest_framework.routers import DefaultRouter
from .views import AttendanceViewSet

router = DefaultRouter()
router.register(
    r"",
    AttendanceViewSet,
    basename="lecture-attendance",
)

urlpatterns = router.urls
