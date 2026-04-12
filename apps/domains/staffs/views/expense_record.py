# PATH: apps/domains/staffs/views/expense_record.py

from django.utils import timezone

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
        serializer.save(tenant_id=tenant.id)

    def perform_update(self, serializer):
        instance = self.get_object()

        if is_month_locked(instance.staff, instance.date):
            raise ValidationError("마감된 월입니다.")

        if instance.status == "APPROVED":
            raise ValidationError("승인된 비용은 수정할 수 없습니다.")

        new_status = serializer.validated_data.get("status", instance.status)

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
            )
            return

        serializer.save()
