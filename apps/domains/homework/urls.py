# PATH: apps/domains/homework/urls.py
"""
Homework URLs

✅ 라우팅
- policies:
    - GET/PATCH /homework/policies/session/?session_id=123
- scores:
    - GET        /homework/scores/
    - PATCH      /homework/scores/{id}/
    - PATCH      /homework/scores/quick/   ← quick patch
"""

from django.urls import path, include
from rest_framework.routers import DefaultRouter

from apps.domains.homework.views import (
    HomeworkScoreViewSet,
    HomeworkPolicyViewSet,
)

router = DefaultRouter()
router.register("scores", HomeworkScoreViewSet, basename="homework-scores")
router.register("policies", HomeworkPolicyViewSet, basename="homework-policies")

urlpatterns = [
    path("", include(router.urls)),
]
