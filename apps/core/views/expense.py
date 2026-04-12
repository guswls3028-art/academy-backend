# PATH: apps/core/views/expense.py
from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.core.permissions import TenantResolvedAndStaff
from apps.core.serializers import ExpenseSerializer
from academy.adapters.db.django import repositories_core as core_repo
from apps.core.services.expense_policy import normalize_expense_amount


# --------------------------------------------------
# Expense (Staff 전용)
# --------------------------------------------------

class MyExpenseViewSet(viewsets.ModelViewSet):
    """
    직원 지출 관리 (tenant 단위 격리)
    """

    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]
    serializer_class = ExpenseSerializer

    def get_queryset(self):
        user = self.request.user
        tenant = getattr(self.request, "tenant", None)
        month = self.request.query_params.get("month")

        qs = core_repo.expense_filter(user=user, tenant=tenant, month=month)
        return qs

    def perform_create(self, serializer):
        tenant = getattr(self.request, "tenant", None)
        if not tenant:
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied("Tenant is required.")
        raw_amount = self.request.data.get("amount")
        serializer.save(
            tenant=tenant,
            user=self.request.user,
            amount=normalize_expense_amount(raw_amount),
        )
