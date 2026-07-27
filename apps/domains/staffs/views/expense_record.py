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
from .helpers import (
    IsPayrollManager,
    StaffDomainPagination,
    is_month_locked,
    can_manage_payroll,
)

# ===========================
# ExpenseRecord
# ===========================

class ExpenseRecordViewSet(viewsets.ModelViewSet):
    serializer_class = ExpenseRecordSerializer
    permission_classes = [IsAuthenticated, IsPayrollManager]
    pagination_class = StaffDomainPagination

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
            # Approval is a separate audited transition. A caller cannot forge
            # an approved expense during creation without approver metadata.
            serializer.save(
                tenant_id=tenant.id,
                staff=locked_staff,
                status="PENDING",
            )

    def perform_destroy(self, instance):
        with transaction.atomic():
            instance = staff_repo.expense_record_get_for_update(
                instance.tenant_id,
                instance.id,
            )
            locked_staff = staff_repo.staff_get_for_update(
                instance.tenant_id,
                instance.staff_id,
            )
            if is_month_locked(locked_staff, instance.date):
                raise ValidationError("마감된 월입니다. 비용을 삭제할 수 없습니다.")
            if instance.status != "PENDING":
                raise ValidationError(
                    "승인·반려된 비용은 이력 보존을 위해 삭제할 수 없습니다."
                )
            instance.delete()

    def perform_update(self, serializer):
        with transaction.atomic():
            stale_instance = serializer.instance
            instance = staff_repo.expense_record_get_for_update(
                stale_instance.tenant_id,
                stale_instance.id,
            )
            serializer.instance = instance
            resulting_staff = serializer.validated_data.get(
                "staff",
                instance.staff,
            )
            resulting_date = serializer.validated_data.get(
                "date",
                instance.date,
            )
            new_status = serializer.validated_data.get(
                "status",
                instance.status,
            )
            locked_staff_by_id = staff_repo.staff_map_for_update(
                instance.tenant_id,
                [instance.staff_id, resulting_staff.id],
            )
            source_staff = locked_staff_by_id[instance.staff_id]
            target_staff = locked_staff_by_id[resulting_staff.id]
            if is_month_locked(source_staff, instance.date):
                raise ValidationError("마감된 월입니다.")
            if is_month_locked(target_staff, resulting_date):
                raise ValidationError(
                    "변경하려는 직원의 해당 월은 마감되어 비용을 이동할 수 없습니다."
                )

            if instance.status != "PENDING":
                raise ValidationError(
                    "승인·반려된 선결제 환급은 수정할 수 없습니다. "
                    "정정이 필요하면 새 항목으로 등록해 주세요."
                )

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

                reviewed = serializer.save(
                    approved_at=timezone.now(),
                    approved_by=self.request.user,
                    **save_kwargs,
                )
                from apps.core.services.ops_audit import record_audit
                record_audit(
                    self.request,
                    action="staff.expense_reviewed",
                    target_tenant=self.request.tenant,
                    summary=f"expense_id={reviewed.id} status={new_status}",
                    payload={
                        "expense_id": reviewed.id,
                        "staff_id": reviewed.staff_id,
                        "status": new_status,
                        "amount": reviewed.amount,
                    },
                )
                return

            serializer.save(**save_kwargs)
