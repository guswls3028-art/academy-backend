# apps/domains/attendance/urls.py

from rest_framework.routers import DefaultRouter
from .views import AttendanceViewSet
from .qr_views import AttendanceQrPointViewSet

router = DefaultRouter()
router.register(r"attendance-qr", AttendanceQrPointViewSet, basename="attendance-qr")
router.register(
    r"attendance",
    AttendanceViewSet,
    basename="lecture-attendance",
)

urlpatterns = router.urls
