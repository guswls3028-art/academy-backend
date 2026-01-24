# PATH: apps/domains/homework/urls.py
# 역할: homework 라우팅 (policy + score endpoints)

"""
Homework URLs

✅ 라우팅
- policies:
    - GET   /homework/policies/?session=
    - PATCH /homework/policies/{id}/
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
    HomeworkEnrollmentManageView,   # ✅ 추가
)

router = DefaultRouter()
router.register("scores", HomeworkScoreViewSet, basename="homework-scores")
router.register("policies", HomeworkPolicyViewSet, basename="homework-policies")

urlpatterns = [
    path("", include(router.urls)),
    path("enrollments/", HomeworkEnrollmentManageView.as_view()),  # ✅ 추가
]
