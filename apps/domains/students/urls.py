from rest_framework.routers import DefaultRouter
from .views import StudentViewSet, TagViewSet

router = DefaultRouter()
router.register(r"", StudentViewSet)
router.register(r"tags", TagViewSet)

urlpatterns = router.urls
