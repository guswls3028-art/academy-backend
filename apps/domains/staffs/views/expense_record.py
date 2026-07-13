# PATH: apps/domains/staffs/views/expense_record.py

from django.utils import timezone
from django.db import transaction

from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import viewsets
from rest_framework.filters import OrderingFilter
from rest_framework.permissions import IsAuthenticated
from rest_framework.exceptions import PermissionDenied, ValidationError

from ..serializers import ExpenseRecordSerializer
from academy.adapters.db.django import repositories_staffs as staff_repo
from ..filters import ExpenseRecordFilter
from .helpers import IsPayrollManager, is_month_locked, can_manage_payroll

# ===========================
# ExpenseRecord
# ===========================

class ExpenseRecordViewSet(viewsets.ModelViewSet):
    serializer_class = ExpenseRecordSerializer
    permission_classes = [IsAuthenticated, IsPayrollManager]

    filter_backends = (DjangoFilterBackend, OrderingFilter)
    filterset_class = ExpenseRecordFilter
    ordering_fields = ["date", "amount", "created_at"]

    def get_queryset(self):
        return staff_repo.expense_record_queryset_tenant(self.request.tenant)

    def perform_create(self, serializer):
        tenant = getattr(self.request, "tenant", None)
        if not tenant:
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied("Tenant is required.")

        staff = serializer.validated_data.get("staff")
        date = serializer.validated_data.get("date")
        if staff is None or date is None:
            raise ValidationError("staff와 date는 필수입니다.")

        with transaction.atomic():
            locked_staff = staff_repo.staff_get_for_update(tenant.id, staff.id)
            if is_month_locked(locked_staff, date):
                raise ValidationError("마감된 월입니다. 비용을 추가할 수 없습니다.")
            serializer.save(tenant_id=tenant.id, staff=locked_staff)

    def perform_destroy(self, instance):
        with transaction.atomic():
            locked_staff = staff_repo.staff_get_for_update(
                instance.tenant_id,
                instance.staff_id,
            )
            instance.refresh_from_db()
            if is_month_locked(locked_staff, instance.date):
                raise ValidationError("마감된 월입니다. 비용을 삭제할 수 없습니다.")
            instance.delete()

    def perform_update(self, serializer):
        instance = serializer.instance

        resulting_staff = serializer.validated_data.get("staff", instance.staff)
        resulting_date = serializer.validated_data.get("date", instance.date)
        new_status = serializer.validated_data.get("status", instance.status)

        with transaction.atomic():
            locked_staff_by_id = staff_repo.staff_map_for_update(
                instance.tenant_id,
                [instance.staff_id, resulting_staff.id],
            )
            source_staff = locked_staff_by_id[instance.staff_id]
            target_staff = locked_staff_by_id[resulting_staff.id]
            instance.refresh_from_db()
            if is_month_locked(source_staff, instance.date):
                raise ValidationError("마감된 월입니다.")
            if is_month_locked(target_staff, resulting_date):
                raise ValidationError(
                    "변경하려는 직원의 해당 월은 마감되어 비용을 이동할 수 없습니다."
                )

            if instance.status == "APPROVED":
                raise ValidationError("승인된 비용은 수정할 수 없습니다.")

            save_kwargs = {}
            if "staff" in serializer.validated_data:
                save_kwargs["staff"] = target_staff

            if new_status != instance.status:
                if not can_manage_payroll(self.request.user, getattr(self.request, "tenant", None)):
                    raise PermissionDenied("관리자만 승인/반려 가능")

                if instance.status != "PENDING":
                    raise ValidationError("이미 처리된 비용입니다.")

                if new_status not in ("APPROVED", "REJECTED"):
                    raise ValidationError("유효하지 않은 상태")

                serializer.save(
                    approved_at=timezone.now(),
                    approved_by=self.request.user,
                    **save_kwargs,
                )
                return

            serializer.save(**save_kwargs)
