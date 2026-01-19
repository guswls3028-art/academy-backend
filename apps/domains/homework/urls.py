# PATH: apps/domains/homework/urls.py

from rest_framework.routers import DefaultRouter

from apps.domains.homework.views import (
    HomeworkScoreViewSet,
    HomeworkPolicyViewSet,
)

router = DefaultRouter()
router.register("scores", HomeworkScoreViewSet, basename="homework-score")
router.register("policies", HomeworkPolicyViewSet, basename="homework-policy")

urlpatterns = router.urls
