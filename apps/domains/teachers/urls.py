# PATH: apps/domains/teachers/urls.py
from rest_framework.routers import DefaultRouter
from .views import TeacherViewSet

router = DefaultRouter()
router.register(r"", TeacherViewSet, basename="teacher")

urlpatterns = router.urls
