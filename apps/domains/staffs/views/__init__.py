# PATH: apps/domains/staffs/views/__init__.py
# Re-export all public symbols for backward compatibility.
# Existing imports like `from apps.domains.staffs.views import X` continue to work.

from .helpers import (
    _owner_display_for_tenant,
    can_access_staff_management,
    IsPayrollManager,
    is_month_locked,
    can_manage_payroll,
    generate_payroll_snapshot,
)
from .work_type import WorkTypeViewSet
from .staff import StaffViewSet
from .staff_work_type import StaffWorkTypeViewSet
from .expense_record import ExpenseRecordViewSet
from .work_month_lock import WorkMonthLockViewSet
from .payroll_snapshot import PayrollSnapshotViewSet
from .work_record import WorkRecordViewSet

__all__ = [
    "_owner_display_for_tenant",
    "can_access_staff_management",
    "IsPayrollManager",
    "is_month_locked",
    "can_manage_payroll",
    "generate_payroll_snapshot",
    "WorkTypeViewSet",
    "StaffViewSet",
    "StaffWorkTypeViewSet",
    "ExpenseRecordViewSet",
    "WorkMonthLockViewSet",
    "PayrollSnapshotViewSet",
    "WorkRecordViewSet",
]
