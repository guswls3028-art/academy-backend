# PATH: apps/domains/staffs/views/work_record.py

from django.db import IntegrityError, transaction
from django.utils import timezone

from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.filters import OrderingFilter
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework.exceptions import PermissionDenied, ValidationError

from academy.adapters.db.django import repositories_staffs as staff_repo

from ..models import WorkRecord
from ..serializers import WorkRecordSerializer
from ..services import OpenWorkRecordConflict, has_open_work_record_conflict
from ..filters import WorkRecordFilter
from apps.core.permissions import TenantResolvedAndStaff
from .helpers import IsPayrollManager, can_manage_payroll, is_month_locked

# ===========================
# WorkRecord (Record 기준: 휴게/종료만)
# ===========================

class WorkRecordViewSet(viewsets.ModelViewSet):
    serializer_class = WorkRecordSerializer
    permission_classes = [IsAuthenticated, IsPayrollManager]

    filter_backends = (DjangoFilterBackend, OrderingFilter)
    filterset_class = WorkRecordFilter
    ordering_fields = ["date", "created_at", "amount"]

    def get_permissions(self):
        if self.action in ("start_break", "end_break", "end_work"):
            return [IsAuthenticated(), TenantResolvedAndStaff()]
        return super().get_permissions()

    def get_queryset(self):
        return (
            WorkRecord.objects
            .filter(tenant=self.request.tenant)
            .select_related("staff", "work_type")
            .order_by("-date", "-start_time")
        )

    def _assert_self_service_or_manager(self, record):
        if can_manage_payroll(self.request.user, record.tenant):
            return
        if record.staff.user_id == self.request.user.id:
            return
        raise PermissionDenied("본인 근무 기록만 처리할 수 있습니다.")

    def perform_create(self, serializer):
        tenant = getattr(self.request, "tenant", None)
        if not tenant:
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied("Tenant is required.")

        staff = serializer.validated_data.get("staff")
        date = serializer.validated_data.get("date")
        if staff is None or date is None:
            raise ValidationError("staff와 date는 필수입니다.")

        try:
            with transaction.atomic():
                locked_staff = staff_repo.staff_get_for_update(
                    tenant.id,
                    staff.pk,
                )
                if is_month_locked(locked_staff, date):
                    raise ValidationError(
                        "마감된 월입니다. 근무기록을 추가할 수 없습니다."
                    )
                if (
                    serializer.validated_data.get("end_time") is None
                    and has_open_work_record_conflict(staff=locked_staff)
                ):
                    raise OpenWorkRecordConflict()
                serializer.save(tenant_id=tenant.id, staff=locked_staff)
        except IntegrityError as exc:
            if (
                serializer.validated_data.get("end_time") is None
                and staff is not None
                and has_open_work_record_conflict(staff=staff)
            ):
                raise OpenWorkRecordConflict() from exc
            raise

    def perform_destroy(self, instance):
        with transaction.atomic():
            locked_staff = staff_repo.staff_get_for_update(
                instance.tenant_id,
                instance.staff_id,
            )
            instance.refresh_from_db()
            if is_month_locked(locked_staff, instance.date):
                raise ValidationError("마감된 월입니다. 근무기록을 삭제할 수 없습니다.")
            instance.delete()

    def perform_update(self, serializer):
        instance = serializer.instance

        resulting_staff = serializer.validated_data.get("staff", instance.staff)
        resulting_date = serializer.validated_data.get("date", instance.date)

        # Direct override fields: admin explicitly sets the final work_hours or amount
        override_fields = {"work_hours", "amount"}
        # Input fields: calculation inputs that should trigger auto-recalculation
        input_fields = {"meal_minutes", "adjustment_amount", "break_minutes",
                        "start_time", "end_time"}

        changed_keys = set(serializer.validated_data.keys())
        has_override = bool(override_fields & changed_keys)
        has_input_change = bool(input_fields & changed_keys)

        try:
            with transaction.atomic():
                locked_staff_by_id = staff_repo.staff_map_for_update(
                    instance.tenant_id,
                    [instance.staff_id, resulting_staff.pk],
                )
                source_staff = locked_staff_by_id[instance.staff_id]
                locked_staff = locked_staff_by_id[resulting_staff.pk]
                if is_month_locked(source_staff, instance.date):
                    raise ValidationError("마감된 월입니다.")
                if is_month_locked(locked_staff, resulting_date):
                    raise ValidationError(
                        "변경하려는 직원의 해당 월은 마감되어 근무기록을 이동할 수 없습니다."
                    )
                resulting_end_time = serializer.validated_data.get(
                    "end_time",
                    instance.end_time,
                )
                if resulting_end_time is None and has_open_work_record_conflict(
                    staff=locked_staff,
                    exclude_record_id=instance.id,
                ):
                    raise OpenWorkRecordConflict()
                save_kwargs = {}
                if "staff" in serializer.validated_data:
                    save_kwargs["staff"] = locked_staff
                if has_override:
                    # Admin directly set work_hours or amount → mark as manually edited
                    serializer.save(is_manually_edited=True, **save_kwargs)
                elif has_input_change and instance.end_time:
                    # Calculation inputs changed → clear manual flag so save() recalculates
                    serializer.save(is_manually_edited=False, **save_kwargs)
                else:
                    serializer.save(**save_kwargs)
        except IntegrityError as exc:
            resulting_end_time = serializer.validated_data.get("end_time", instance.end_time)
            if resulting_end_time is None and has_open_work_record_conflict(
                staff=resulting_staff,
                exclude_record_id=instance.id,
            ):
                raise OpenWorkRecordConflict() from exc
            raise

    @action(detail=True, methods=["post"], url_path="recalculate")
    @transaction.atomic
    def recalculate(self, request, pk=None):
        """수동 수정 플래그를 해제하고 자동 재계산. 관리자가 '자동 계산으로 복원' 시 사용."""
        record = self.get_object()
        locked_staff = staff_repo.staff_get_for_update(
            record.tenant_id,
            record.staff_id,
        )
        record.refresh_from_db()

        if is_month_locked(locked_staff, record.date):
            raise ValidationError("마감된 월입니다.")

        if not record.end_time:
            raise ValidationError("퇴근 시간이 없어 계산할 수 없습니다.")

        record.is_manually_edited = False
        record.save()  # save() will auto-calculate since is_manually_edited=False

        return Response(WorkRecordSerializer(record).data)

    @action(detail=True, methods=["post"])
    @transaction.atomic
    def start_break(self, request, pk=None):
        record = self.get_object()
        self._assert_self_service_or_manager(record)
        locked_staff = staff_repo.staff_get_for_update(
            record.tenant_id,
            record.staff_id,
        )
        record.refresh_from_db()

        if is_month_locked(locked_staff, record.date):
            raise ValidationError("마감된 월입니다.")

        if record.current_break_started_at:
            raise ValidationError("이미 휴게 중입니다.")

        record.current_break_started_at = timezone.localtime(timezone.now())
        record.save(update_fields=["current_break_started_at"])

        return Response({"status": "BREAK_STARTED"})

    @action(detail=True, methods=["post"])
    @transaction.atomic
    def end_break(self, request, pk=None):
        record = self.get_object()
        self._assert_self_service_or_manager(record)
        locked_staff = staff_repo.staff_get_for_update(
            record.tenant_id,
            record.staff_id,
        )
        record.refresh_from_db()

        if is_month_locked(locked_staff, record.date):
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
    @transaction.atomic
    def end_work(self, request, pk=None):
        record = self.get_object()
        self._assert_self_service_or_manager(record)
        locked_staff = staff_repo.staff_get_for_update(
            record.tenant_id,
            record.staff_id,
        )
        record.refresh_from_db()

        if is_month_locked(locked_staff, record.date):
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
                parsed_meal_minutes = int(meal_minutes)
            except (TypeError, ValueError) as exc:
                raise ValidationError("meal_minutes는 0 이상의 정수여야 합니다.") from exc
            if parsed_meal_minutes < 0:
                raise ValidationError("meal_minutes는 0 이상의 정수여야 합니다.")
            record.meal_minutes = parsed_meal_minutes

        adjustment_amount = request.data.get("adjustment_amount")
        if adjustment_amount is not None:
            try:
                record.adjustment_amount = int(adjustment_amount)
            except (TypeError, ValueError) as exc:
                raise ValidationError("adjustment_amount는 정수여야 합니다.") from exc

        record.end_time = timezone.localtime(timezone.now()).time()
        # save() auto-calculates work_hours, amount, resolved_hourly_wage
        record.save()

        return Response(WorkRecordSerializer(record).data)
