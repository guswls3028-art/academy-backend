from rest_framework.routers import DefaultRouter
from .views import TeacherViewSet

router = DefaultRouter()
router.register(r"", TeacherViewSet)

urlpatterns = router.urls
