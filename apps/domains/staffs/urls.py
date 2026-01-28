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
# ⚠️ 서브 리소스 먼저 등록
# ===========================
router.register(r"work-types", WorkTypeViewSet)
router.register(r"staff-work-types", StaffWorkTypeViewSet)
router.register(r"work-records", WorkRecordViewSet)
router.register(r"expense-records", ExpenseRecordViewSet)
router.register(r"work-month-locks", WorkMonthLockViewSet)
router.register(
    r"payroll-snapshots",
    PayrollSnapshotViewSet,
    basename="payroll-snapshot",
)

# ===========================
# ⚠️ Staff는 반드시 마지막
# ===========================
router.register(r"", StaffViewSet)

urlpatterns = [
    path("", include(router.urls)),
]
