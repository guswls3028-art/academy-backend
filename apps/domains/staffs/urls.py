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

# ===========================
# 서브 리소스
# ===========================
router.register(r"work-types", WorkTypeViewSet, basename="work-type")
router.register(r"staff-work-types", StaffWorkTypeViewSet, basename="staff-work-type")
router.register(r"work-records", WorkRecordViewSet, basename="work-record")
router.register(r"expense-records", ExpenseRecordViewSet, basename="expense-record")
router.register(r"work-month-locks", WorkMonthLockViewSet, basename="work-month-lock")
router.register(
    r"payroll-snapshots",
    PayrollSnapshotViewSet,
    basename="payroll-snapshot",
)

# ===========================
# Staff (루트)
# ===========================
router.register(r"", StaffViewSet, basename="staff")

urlpatterns = [
    path("", include(router.urls)),
]
