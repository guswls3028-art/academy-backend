"""Use the native month-close operation for the isolated staff fixtures."""

from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import serializers
from rest_framework.exceptions import NotFound, ValidationError

from apps.domains.staffs.views.work_month_lock import WorkMonthLockViewSet
from apps.domains.students.services.dawon_staff import sample_staff

from .dawon_staff import StaffRequest


class PayrollCloseInput(serializers.Serializer):
    staff = serializers.IntegerField(min_value=1, max_value=2**63-1)
    year = serializers.IntegerField(min_value=2020, max_value=2100)
    month = serializers.IntegerField(min_value=1, max_value=12)


class SyntheticMonthLock(WorkMonthLockViewSet):
    def close_staff(self, request, staff_id):
        staff = super().close_staff(request, staff_id)
        if not sample_staff(request.tenant).filter(pk=staff.pk).exists():
            raise NotFound()
        return staff


class DawonPayrollCloseView(StaffRequest):
    @extend_schema(exclude=True)
    def post(self, request):
        payload = PayrollCloseInput(data=request.data)
        payload.is_valid(raise_exception=True)
        period = payload.validated_data
        today = timezone.localdate()
        if (period["year"], period["month"]) >= (today.year, today.month):
            raise ValidationError("종료된 달만 마감할 수 있습니다.")
        # This handler owns the Staff lock, blockers, and append-only snapshot.
        return SyntheticMonthLock().create(request)
