# PATH: apps/domains/homework/urls.py
from rest_framework.routers import DefaultRouter

from apps.domains.homework.views import HomeworkScoreViewSet

router = DefaultRouter()
router.register("scores", HomeworkScoreViewSet, basename="homework-score")

urlpatterns = router.urls
