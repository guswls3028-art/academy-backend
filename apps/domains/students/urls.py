# PATH: apps/domains/students/urls.py

from rest_framework.routers import DefaultRouter
from .views import StudentViewSet, TagViewSet

router = DefaultRouter()

# 🔥 basename 명시 (queryset 없는 ViewSet 대응)
router.register(r"tags", TagViewSet, basename="student-tag")
router.register(r"", StudentViewSet, basename="student")


urlpatterns = router.urls
