from rest_framework.routers import DefaultRouter
from .views import (
    MaterialViewSet,
    MaterialCategoryViewSet,
    MaterialAccessViewSet,
)

router = DefaultRouter()
router.register("materials", MaterialViewSet)
router.register("material-categories", MaterialCategoryViewSet)
router.register("material-accesses", MaterialAccessViewSet)

urlpatterns = router.urls
