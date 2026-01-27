# PATH: apps/domains/staffs/urls.py
from django.urls import path, include
from rest_framework.routers import DefaultRouter

from .views import (
    WorkTypeViewSet,
    StaffViewSet,
    StaffWorkTypeViewSet,
    WorkRecordViewSet,
    ExpenseRecordViewSet,
    WorkMonthLockViewSet,
    PayrollSnapshotViewSet,
)

router = DefaultRouter()
router.register(r"work-types", WorkTypeViewSet)
router.register(r"staffs", StaffViewSet)
router.register(r"staff-work-types", StaffWorkTypeViewSet)
router.register(r"work-records", WorkRecordViewSet)
router.register(r"expense-records", ExpenseRecordViewSet)
router.register(r"work-month-locks", WorkMonthLockViewSet)
router.register(r"payroll-snapshots", PayrollSnapshotViewSet, basename="payroll-snapshot")

urlpatterns = [path("", include(router.urls))]
