# PATH: apps/domains/staffs/views/work_record.py

from django.utils import timezone

from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.filters import OrderingFilter
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework.exceptions import ValidationError

from ..models import WorkRecord
from ..serializers import WorkRecordSerializer
from ..filters import WorkRecordFilter
from .helpers import IsPayrollManager, is_month_locked

# ===========================
# WorkRecord (Record 기준: 휴게/종료만)
# ===========================

class WorkRecordViewSet(viewsets.ModelViewSet):
    serializer_class = WorkRecordSerializer
    permission_classes = [IsAuthenticated, IsPayrollManager]

    filter_backends = (DjangoFilterBackend, OrderingFilter)
    filterset_class = WorkRecordFilter
    ordering_fields = ["date", "created_at", "amount"]

    def get_queryset(self):
        return (
            WorkRecord.objects
            .filter(tenant=self.request.tenant)
            .select_related("staff", "work_type")
            .order_by("-date", "-start_time")
        )

    def perform_create(self, serializer):
        tenant = getattr(self.request, "tenant", None)
        if not tenant:
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied("Tenant is required.")
        serializer.save(tenant_id=tenant.id)

    def perform_update(self, serializer):
        instance = serializer.instance

        if is_month_locked(instance.staff, instance.date):
            raise ValidationError("마감된 월입니다.")

        # Direct override fields: admin explicitly sets the final work_hours or amount
        override_fields = {"work_hours", "amount"}
        # Input fields: calculation inputs that should trigger auto-recalculation
        input_fields = {"meal_minutes", "adjustment_amount", "break_minutes",
                        "start_time", "end_time"}

        changed_keys = set(serializer.validated_data.keys())
        has_override = bool(override_fields & changed_keys)
        has_input_change = bool(input_fields & changed_keys)

        if has_override:
            # Admin directly set work_hours or amount → mark as manually edited
            serializer.save(is_manually_edited=True)
        elif has_input_change and instance.end_time:
            # Calculation inputs changed → clear manual flag so save() recalculates
            serializer.save(is_manually_edited=False)
        else:
            serializer.save()

    @action(detail=True, methods=["post"], url_path="recalculate")
    def recalculate(self, request, pk=None):
        """수동 수정 플래그를 해제하고 자동 재계산. 관리자가 '자동 계산으로 복원' 시 사용."""
        record = self.get_object()

        if is_month_locked(record.staff, record.date):
            raise ValidationError("마감된 월입니다.")

        if not record.end_time:
            raise ValidationError("퇴근 시간이 없어 계산할 수 없습니다.")

        record.is_manually_edited = False
        record.save()  # save() will auto-calculate since is_manually_edited=False

        return Response(WorkRecordSerializer(record).data)

    @action(detail=True, methods=["post"])
    def start_break(self, request, pk=None):
        record = self.get_object()

        if is_month_locked(record.staff, record.date):
            raise ValidationError("마감된 월입니다.")

        if record.current_break_started_at:
            raise ValidationError("이미 휴게 중입니다.")

        record.current_break_started_at = timezone.localtime(timezone.now())
        record.save(update_fields=["current_break_started_at"])

        return Response({"status": "BREAK_STARTED"})

    @action(detail=True, methods=["post"])
    def end_break(self, request, pk=None):
        record = self.get_object()

        if is_month_locked(record.staff, record.date):
            raise ValidationError("마감된 월입니다.")

        if not record.current_break_started_at:
            raise ValidationError("휴게 중이 아닙니다.")

        now = timezone.localtime(timezone.now())
        delta = now - record.current_break_started_at
        delta_seconds = int(delta.total_seconds())
        record.break_total_seconds = getattr(record, "break_total_seconds", 0) + delta_seconds
        record.break_minutes = record.break_total_seconds // 60
        record.current_break_started_at = None
        record.save(update_fields=["break_minutes", "break_total_seconds", "current_break_started_at"])

        return Response({"status": "BREAK_ENDED"})

    @action(detail=True, methods=["post"])
    def end_work(self, request, pk=None):
        record = self.get_object()

        if is_month_locked(record.staff, record.date):
            raise ValidationError("마감된 월입니다.")

        if record.end_time:
            raise ValidationError("이미 종료된 근무입니다.")

        if record.current_break_started_at:
            now = timezone.localtime(timezone.now())
            delta = now - record.current_break_started_at
            delta_seconds = int(delta.total_seconds())
            record.break_total_seconds = getattr(record, "break_total_seconds", 0) + delta_seconds
            record.break_minutes = record.break_total_seconds // 60
            record.current_break_started_at = None

        # Accept optional meal_minutes and adjustment_amount from request
        meal_minutes = request.data.get("meal_minutes")
        if meal_minutes is not None:
            try:
                record.meal_minutes = max(0, int(meal_minutes))
            except (TypeError, ValueError):
                pass

        adjustment_amount = request.data.get("adjustment_amount")
        if adjustment_amount is not None:
            try:
                record.adjustment_amount = int(adjustment_amount)
            except (TypeError, ValueError):
                pass

        record.end_time = timezone.localtime(timezone.now()).time()
        # save() auto-calculates work_hours, amount, resolved_hourly_wage
        record.save()

        return Response(WorkRecordSerializer(record).data)
