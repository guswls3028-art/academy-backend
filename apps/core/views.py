# PATH: apps/core/views.py
from datetime import datetime
from django.db.models import Sum

from rest_framework.views import APIView
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.response import Response

from drf_yasg.utils import swagger_auto_schema

from apps.core.models import Attendance, Expense, Program
from apps.core.permissions import (
    TenantResolved,
    TenantResolvedAndMember,
    TenantResolvedAndStaff,
)
from apps.core.serializers import (
    UserSerializer,
    ProfileSerializer,
    AttendanceSerializer,
    ExpenseSerializer,
    ProgramPublicSerializer,
    ProgramUpdateSerializer,
)
from apps.core.services.attendance_policy import calculate_duration_hours, calculate_amount
from apps.core.services.expense_policy import normalize_expense_amount


# --------------------------------------------------
# Auth: /core/me/
# --------------------------------------------------

class MeView(APIView):
    """
    ✅ Core Auth Endpoint (Enterprise Final)

    - 인증 필수
    - tenant 확정 필수
    - TenantMembership 존재 필수
    - tenant 기준 role 을 tenantRole 로 반환
    - 프론트는 이 응답만 신뢰 (SSOT)
    """

    permission_classes = [IsAuthenticated, TenantResolvedAndMember]

    @swagger_auto_schema(auto_schema=None)
    def get(self, request):
        serializer = UserSerializer(
            request.user,
            context={"request": request},  # ✅ 핵심
        )
        return Response(serializer.data)


# --------------------------------------------------
# Program: /core/program/
# --------------------------------------------------

class ProgramView(APIView):
    """
    ✅ Program SSOT Endpoint (Enterprise)

    GET  /api/v1/core/program/
      - 로그인 전 AllowAny
      - tenant resolve 필수
      - DB write 발생 금지 (read-only 보장)

    PATCH /api/v1/core/program/
      - Staff only
      - tenant resolve 필수
      - 해당 tenant의 Program만 수정 가능 (1:1)
    """

    @swagger_auto_schema(auto_schema=None)
    def get(self, request):
        tenant = getattr(request, "tenant", None)
        if tenant is None:
            return Response({"detail": "tenant must be resolved"}, status=400)

        program = Program.objects.filter(tenant=tenant).first()
        if program is None:
            # 운영에서는 Tenant 생성 시 signal으로 Program 생성. 없으면 404 (프론트에서 처리)
            return Response(
                {
                    "detail": "program not initialized for tenant",
                    "code": "program_missing",
                    "tenant": tenant.code,
                },
                status=404,
            )

        data = ProgramPublicSerializer(program).data
        return Response(data)

    @swagger_auto_schema(auto_schema=None)
    def patch(self, request):
        tenant = getattr(request, "tenant", None)
        if tenant is None:
            return Response({"detail": "tenant must be resolved"}, status=400)

        program = Program.objects.filter(tenant=tenant).first()
        if program is None:
            return Response(
                {
                    "detail": "program not initialized for tenant",
                    "code": "program_missing",
                    "tenant": tenant.code,
                },
                status=404,
            )

        serializer = ProgramUpdateSerializer(
            program,
            data=(request.data if isinstance(request.data, dict) else {}),
            partial=True,
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()

        return Response(ProgramPublicSerializer(program).data)

    def get_permissions(self):
        if self.request.method == "GET":
            return [AllowAny(), TenantResolved()]
        return [IsAuthenticated(), TenantResolvedAndStaff()]


# --------------------------------------------------
# Profile (Staff 영역)
# --------------------------------------------------

class ProfileViewSet(viewsets.ViewSet):
    """
    직원/강사/관리자 전용 Profile API
    """

    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    @swagger_auto_schema(auto_schema=None)
    @action(detail=False, methods=["get"])
    def me(self, request):
        serializer = ProfileSerializer(request.user)
        return Response(serializer.data)

    @swagger_auto_schema(auto_schema=None)
    @action(detail=False, methods=["patch"])
    def update_me(self, request):
        serializer = ProfileSerializer(
            request.user,
            data=request.data,
            partial=True,
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)

    @swagger_auto_schema(auto_schema=None)
    @action(detail=False, methods=["post"], url_path="change-password")
    def change_password(self, request):
        old_pw = request.data.get("old_password")
        new_pw = request.data.get("new_password")

        if not old_pw or not new_pw:
            return Response({"error": "old_password, new_password 필요"}, status=400)

        if not request.user.check_password(old_pw):
            return Response({"error": "현재 비밀번호가 올바르지 않습니다."}, status=400)

        request.user.set_password(new_pw)
        request.user.save()

        return Response({"message": "비밀번호 변경 완료"})


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

        qs = Attendance.objects.filter(
            user=user,
            tenant=tenant,
        )

        if month:
            qs = qs.filter(date__startswith=month)

        return qs

    def perform_create(self, serializer):
        user = self.request.user
        tenant = getattr(self.request, "tenant", None)

        start = self.request.data.get("start_time")
        end = self.request.data.get("end_time")

        duration = calculate_duration_hours(start, end)
        amount = calculate_amount(tenant, duration) if tenant is not None else 0

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

        qs = Attendance.objects.filter(
            user=user,
            tenant=tenant,
        )

        if month:
            qs = qs.filter(date__startswith=month)

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

        qs = Expense.objects.filter(
            user=user,
            tenant=tenant,
        )

        if month:
            qs = qs.filter(date__startswith=month)

        return qs

    def perform_create(self, serializer):
        tenant = getattr(self.request, "tenant", None)
        raw_amount = self.request.data.get("amount")
        serializer.save(
            tenant=tenant,
            user=self.request.user,
            amount=normalize_expense_amount(raw_amount),
        )
