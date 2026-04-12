# PATH: apps/domains/staffs/views/work_month_lock.py

from rest_framework import viewsets, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.exceptions import ValidationError

from ..models import Staff
from ..serializers import WorkMonthLockSerializer
from academy.adapters.db.django import repositories_staffs as staff_repo
from .helpers import IsPayrollManager, generate_payroll_snapshot

# ===========================
# WorkMonthLock
# ===========================

class WorkMonthLockViewSet(viewsets.ModelViewSet):
    serializer_class = WorkMonthLockSerializer
    permission_classes = [IsAuthenticated, IsPayrollManager]

    def get_queryset(self):
        return staff_repo.work_month_lock_queryset_tenant(self.request.tenant)

    def create(self, request, *args, **kwargs):
        staff_id = request.data.get("staff")
        year_raw = request.data.get("year")
        month_raw = request.data.get("month")
        if staff_id is None:
            raise ValidationError("staff는 필수입니다.")
        if year_raw is None or month_raw is None:
            raise ValidationError("year, month는 필수입니다.")
        try:
            year = int(year_raw)
            month = int(month_raw)
        except (TypeError, ValueError):
            raise ValidationError("year, month는 숫자여야 합니다.")
        if not (1 <= month <= 12):
            raise ValidationError("month는 1~12 사이여야 합니다.")

        try:
            staff = staff_repo.staff_get(request.tenant, staff_id)
        except Staff.DoesNotExist:
            raise ValidationError("해당 직원을 찾을 수 없습니다.")

        obj, _ = staff_repo.work_month_lock_update_or_create_defaults(
            request.tenant,
            staff,
            year,
            month,
            defaults={"is_locked": True, "locked_by": request.user},
        )

        generate_payroll_snapshot(
            staff=staff,
            year=year,
            month=month,
            user=request.user,
        )

        return Response(
            WorkMonthLockSerializer(obj).data,
            status=status.HTTP_201_CREATED,
        )
