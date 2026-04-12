# PATH: apps/core/views/attendance.py
from django.db.models import Sum

from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from drf_yasg.utils import swagger_auto_schema

from apps.core.permissions import TenantResolvedAndStaff
from apps.core.serializers import AttendanceSerializer
from academy.adapters.db.django import repositories_core as core_repo
from apps.core.services.attendance_policy import calculate_duration_hours, calculate_amount


# --------------------------------------------------
# Attendance (Staff 전용)
# --------------------------------------------------

class MyAttendanceViewSet(viewsets.ModelViewSet):
    """
    직원 근태 관리 (tenant 단위 격리)
    """

    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]
    serializer_class = AttendanceSerializer

    def get_queryset(self):
        user = self.request.user
        tenant = getattr(self.request, "tenant", None)
        month = self.request.query_params.get("month")

        qs = core_repo.attendance_filter(user=user, tenant=tenant, month=month)
        return qs

    def perform_create(self, serializer):
        user = self.request.user
        tenant = getattr(self.request, "tenant", None)
        if not tenant:
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied("Tenant is required.")

        start = self.request.data.get("start_time")
        end = self.request.data.get("end_time")

        duration = calculate_duration_hours(start, end)
        amount = calculate_amount(tenant, duration)

        serializer.save(
            tenant=tenant,
            user=user,
            duration_hours=duration,
            amount=amount,
        )

    @swagger_auto_schema(auto_schema=None)
    @action(detail=False, methods=["get"], url_path="summary")
    def summary(self, request):
        user = request.user
        tenant = getattr(self.request, "tenant", None)
        month = self.request.query_params.get("month")

        qs = core_repo.attendance_filter(user=user, tenant=tenant, month=month)
        total_hours = qs.aggregate(Sum("duration_hours"))["duration_hours__sum"] or 0
        total_amount = qs.aggregate(Sum("amount"))["amount__sum"] or 0
        after_tax = int(total_amount * 0.967)

        return Response(
            {
                "total_hours": total_hours,
                "total_amount": total_amount,
                "total_after_tax": after_tax,
            }
        )
