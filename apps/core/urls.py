# apps/core/urls.py

from django.urls import path, include
from rest_framework.routers import DefaultRouter

from apps.core.views import (
    MeView,
    ProfileViewSet,
    MyAttendanceViewSet,
    MyExpenseViewSet,
)

router = DefaultRouter()
router.register("profile", ProfileViewSet, basename="profile")
router.register("profile/attendance", MyAttendanceViewSet, basename="my-attendance")
router.register("profile/expenses", MyExpenseViewSet, basename="my-expense")

urlpatterns = [
    path("me/", MeView.as_view(), name="core-me"),
    path("", include(router.urls)),
]
