from django.urls import path, include
from rest_framework.routers import DefaultRouter

from .views import (
    WorkTypeViewSet,
    StaffViewSet,
    StaffWorkTypeViewSet,
    WorkRecordViewSet,
    ExpenseRecordViewSet,
)

router = DefaultRouter()
router.register(r"work-types", WorkTypeViewSet, basename="work-type")
router.register(r"staffs", StaffViewSet, basename="staff")
router.register(r"staff-work-types", StaffWorkTypeViewSet, basename="staff-work-type")
router.register(r"work-records", WorkRecordViewSet, basename="work-record")
router.register(r"expense-records", ExpenseRecordViewSet, basename="expense-record")

urlpatterns = [
    path("", include(router.urls)),
]
