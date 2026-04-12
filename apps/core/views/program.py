# PATH: apps/core/views/program.py
import logging
from django.conf import settings

from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.response import Response

from drf_yasg.utils import swagger_auto_schema

from apps.core.permissions import (
    TenantResolved,
    TenantResolvedAndStaff,
)
from apps.core.serializers import (
    ProgramPublicSerializer,
    ProgramUpdateSerializer,
)
from academy.adapters.db.django import repositories_core as core_repo

logger = logging.getLogger(__name__)


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

        try:
            program = core_repo.program_get_by_tenant(tenant)
        except Exception as e:
            logger.exception("ProgramView get program_get_by_tenant failed: %s", e)
            payload = {"detail": "서버 오류가 발생했습니다."}
            if getattr(settings, "DEBUG", False):
                payload["error"] = str(e)
            return Response(
                payload,
                status=500,
            )
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

        try:
            data = ProgramPublicSerializer(program).data
            return Response(data)
        except Exception as e:
            logger.exception("ProgramView get serialize failed: %s", e)
            payload = {"detail": "서버 오류가 발생했습니다."}
            if getattr(settings, "DEBUG", False):
                payload["error"] = str(e)
            return Response(
                payload,
                status=500,
            )

    @swagger_auto_schema(auto_schema=None)
    def patch(self, request):
        tenant = getattr(request, "tenant", None)
        if tenant is None:
            return Response({"detail": "tenant must be resolved"}, status=400)

        program = core_repo.program_get_by_tenant(tenant)
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
            context={"request": request},
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()

        return Response(ProgramPublicSerializer(program).data)

    def get_permissions(self):
        if self.request.method == "GET":
            return [AllowAny(), TenantResolved()]
        return [IsAuthenticated(), TenantResolvedAndStaff()]


# --------------------------------------------------
# Subscription: /core/subscription/
# --------------------------------------------------

class SubscriptionView(APIView):
    """
    GET /api/v1/core/subscription/
    현재 tenant의 구독 정보 반환. 로그인 전에도 접근 가능 (AllowAny + TenantResolved).
    프론트에서 구독 상태 UI, 결제 탭, 만료 알림에 사용.
    """

    permission_classes = [AllowAny, TenantResolved]

    @swagger_auto_schema(auto_schema=None)
    def get(self, request):
        tenant = getattr(request, "tenant", None)
        if tenant is None:
            return Response({"detail": "tenant must be resolved"}, status=400)

        program = core_repo.program_get_by_tenant(tenant)
        if program is None:
            return Response(
                {"detail": "program not initialized", "code": "program_missing"},
                status=404,
            )

        # Promo/discount calculation
        from apps.core.models.program import Program as ProgramModel
        original_price = ProgramModel.PLAN_PRICES.get(program.plan, program.monthly_price)
        is_promo = program.monthly_price < original_price
        discount_rate = round((1 - program.monthly_price / original_price) * 100) if is_promo and original_price > 0 else 0

        # 해지 예약 상태 표시
        if program.cancel_at_period_end:
            status_display = f"{program.get_subscription_status_display()} (해지 예약)"
        else:
            status_display = program.get_subscription_status_display()

        return Response({
            "plan": program.plan,
            "plan_display": program.get_plan_display(),
            "monthly_price": program.monthly_price,
            "original_price": original_price,
            "is_promo": is_promo,
            "discount_rate": discount_rate,
            "subscription_status": program.subscription_status,
            "subscription_status_display": status_display,
            "subscription_started_at": str(program.subscription_started_at) if program.subscription_started_at else None,
            "subscription_expires_at": str(program.subscription_expires_at) if program.subscription_expires_at else None,
            "is_subscription_active": program.is_subscription_active,
            "days_remaining": program.days_remaining,
            "billing_email": program.billing_email,
            "billing_mode": program.billing_mode,
            "next_billing_at": str(program.next_billing_at) if program.next_billing_at else None,
            "cancel_at_period_end": program.cancel_at_period_end,
            "canceled_at": str(program.canceled_at) if program.canceled_at else None,
            "tenant_code": tenant.code,
            "tenant_name": tenant.name or "",
        })
