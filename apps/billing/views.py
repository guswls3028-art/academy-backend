"""
Billing API Views.

두 가지 영역:
1. 플랫폼 관리자 API (Tenant 1 owner) — 크로스 테넌트 조회/관리
2. 원장 API (각 테넌트 owner) — 자기 테넌트 결제 관리
"""

from rest_framework import generics, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.billing.models import Invoice, PaymentTransaction, BillingKey, BillingProfile
from apps.billing.serializers import (
    BillingKeySerializer,
    BillingProfileSerializer,
    ChangePlanSerializer,
    ExtendSubscriptionSerializer,
    InvoiceDetailSerializer,
    InvoiceListSerializer,
    MarkPaidSerializer,
    PaymentTransactionSerializer,
    TenantSubscriptionSummarySerializer,
)
from apps.billing.services import billing_key_service, invoice_service, subscription_service
from apps.core.models.program import Program
from apps.core.permissions import (
    IsSuperuserOnly,
    TenantResolvedAndOwner,
    TenantResolvedAndStaff,
    is_platform_admin_tenant,
)


# ══════════════════════════════════════════════
# 1. 플랫폼 관리자 API (Superuser / Tenant 1 owner)
# ══════════════════════════════════════════════

class AdminTenantSubscriptionListView(APIView):
    """
    GET /api/v1/billing/admin/tenants/
    전체 테넌트 구독 현황 조회 (플랫폼 관리자 전용).
    """
    permission_classes = [IsAuthenticated, IsSuperuserOnly]

    def get(self, request):
        programs = Program.objects.select_related("tenant").order_by("tenant__code")
        data = []
        for p in programs:
            data.append({
                "tenant_id": p.tenant_id,
                "tenant_code": p.tenant.code,
                "tenant_name": p.tenant.name or "",
                "plan": p.plan,
                "plan_display": p.get_plan_display(),
                "monthly_price": p.monthly_price,
                "subscription_status": p.subscription_status,
                "subscription_status_display": p.get_subscription_status_display(),
                "subscription_expires_at": p.subscription_expires_at,
                "days_remaining": p.days_remaining,
                "billing_mode": p.billing_mode,
                "cancel_at_period_end": p.cancel_at_period_end,
                "next_billing_at": p.next_billing_at,
                "is_subscription_active": p.is_subscription_active,
            })
        serializer = TenantSubscriptionSummarySerializer(data, many=True)
        return Response(serializer.data)


class AdminExtendSubscriptionView(APIView):
    """
    POST /api/v1/billing/admin/tenants/{program_id}/extend/
    수동 구독 기간 연장 (플랫폼 관리자 전용).
    """
    permission_classes = [IsAuthenticated, IsSuperuserOnly]

    def post(self, request, program_id):
        serializer = ExtendSubscriptionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            program = subscription_service.extend(program_id, serializer.validated_data["days"])
        except Program.DoesNotExist:
            return Response({"detail": "Program not found"}, status=status.HTTP_404_NOT_FOUND)

        return Response({
            "tenant_code": program.tenant.code,
            "subscription_status": program.subscription_status,
            "subscription_expires_at": str(program.subscription_expires_at),
            "days_remaining": program.days_remaining,
        })


class AdminChangePlanView(APIView):
    """
    POST /api/v1/billing/admin/tenants/{program_id}/change-plan/
    플랜 변경 (플랫폼 관리자 전용).
    """
    permission_classes = [IsAuthenticated, IsSuperuserOnly]

    def post(self, request, program_id):
        serializer = ChangePlanSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            program = subscription_service.change_plan(
                program_id, serializer.validated_data["plan"]
            )
        except Program.DoesNotExist:
            return Response({"detail": "Program not found"}, status=status.HTTP_404_NOT_FOUND)
        except ValueError as e:
            return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)

        return Response({
            "tenant_code": program.tenant.code,
            "plan": program.plan,
            "plan_display": program.get_plan_display(),
            "monthly_price": program.monthly_price,
        })


class AdminInvoiceListView(generics.ListAPIView):
    """
    GET /api/v1/billing/admin/invoices/
    전체 인보이스 목록 (플랫폼 관리자 전용).
    """
    permission_classes = [IsAuthenticated, IsSuperuserOnly]
    serializer_class = InvoiceListSerializer

    def get_queryset(self):
        qs = Invoice.objects.select_related("tenant").order_by("-created_at")
        # 필터링
        status_filter = self.request.query_params.get("status")
        if status_filter:
            qs = qs.filter(status=status_filter.upper())
        tenant_code = self.request.query_params.get("tenant")
        if tenant_code:
            qs = qs.filter(tenant__code=tenant_code)
        return qs


class AdminInvoiceDetailView(generics.RetrieveAPIView):
    """
    GET /api/v1/billing/admin/invoices/{pk}/
    인보이스 상세 (플랫폼 관리자 전용).
    """
    permission_classes = [IsAuthenticated, IsSuperuserOnly]
    serializer_class = InvoiceDetailSerializer
    queryset = Invoice.objects.select_related("tenant")


class AdminMarkInvoicePaidView(APIView):
    """
    POST /api/v1/billing/admin/invoices/{pk}/mark-paid/
    수동 입금 확인 (플랫폼 관리자 전용).
    """
    permission_classes = [IsAuthenticated, IsSuperuserOnly]

    def post(self, request, pk):
        try:
            inv = Invoice.objects.get(pk=pk)
        except Invoice.DoesNotExist:
            return Response({"detail": "Invoice not found"}, status=status.HTTP_404_NOT_FOUND)

        if inv.status == "PAID":
            return Response(
                {"detail": f"이미 결제 완료 (paid_at: {inv.paid_at})"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if inv.status not in ("PENDING", "OVERDUE", "FAILED"):
            return Response(
                {"detail": f"입금 확인 불가 상태: {inv.status}"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # FAILED → PENDING 먼저
        if inv.status == "FAILED":
            invoice_service.retry_pending(inv.pk)

        inv = invoice_service.mark_paid(inv.pk)
        return Response(InvoiceDetailSerializer(inv).data)


class AdminDashboardView(APIView):
    """
    GET /api/v1/billing/admin/dashboard/
    결제 대시보드 요약 (플랫폼 관리자 전용).
    """
    permission_classes = [IsAuthenticated, IsSuperuserOnly]

    def get(self, request):
        from datetime import date, timedelta
        from django.conf import settings
        from django.db.models import Count, Sum, Q

        today = date.today()
        exempt = settings.BILLING_EXEMPT_TENANT_IDS
        programs = Program.objects.exclude(tenant_id__in=exempt)

        # MRR (active 테넌트 기준)
        mrr = programs.filter(
            subscription_status="active"
        ).aggregate(total=Sum("monthly_price"))["total"] or 0

        # 상태별 테넌트 수
        status_counts = dict(
            programs.values_list("subscription_status").annotate(c=Count("id")).values_list("subscription_status", "c")
        )

        # 만료 임박 (7일 이내)
        expiring_soon = programs.filter(
            subscription_status="active",
            subscription_expires_at__lte=today + timedelta(days=7),
            subscription_expires_at__gte=today,
        ).count()

        # 연체/실패 인보이스
        overdue_invoices = Invoice.objects.filter(
            status__in=["OVERDUE", "FAILED"],
        ).exclude(tenant_id__in=exempt).count()

        # 플랜별 분포
        plan_dist = dict(
            programs.values_list("plan").annotate(c=Count("id")).values_list("plan", "c")
        )

        return Response({
            "mrr": mrr,
            "status_counts": status_counts,
            "expiring_soon": expiring_soon,
            "overdue_invoices": overdue_invoices,
            "plan_distribution": plan_dist,
            "total_tenants": programs.count(),
        })


# ══════════════════════════════════════════════
# 2. 원장 API (각 테넌트)
# ══════════════════════════════════════════════

class MyInvoiceListView(generics.ListAPIView):
    """
    GET /api/v1/billing/invoices/
    내 테넌트 인보이스 목록.
    """
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]
    serializer_class = InvoiceListSerializer

    def get_queryset(self):
        tenant = getattr(self.request, "tenant", None)
        return Invoice.objects.filter(tenant=tenant).order_by("-created_at")


class MyInvoiceDetailView(generics.RetrieveAPIView):
    """
    GET /api/v1/billing/invoices/{pk}/
    내 테넌트 인보이스 상세.
    """
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]
    serializer_class = InvoiceDetailSerializer

    def get_queryset(self):
        tenant = getattr(self.request, "tenant", None)
        return Invoice.objects.filter(tenant=tenant)


class MyBillingKeyListView(APIView):
    """
    GET /api/v1/billing/cards/
    등록된 카드 목록.
    """
    permission_classes = [IsAuthenticated, TenantResolvedAndOwner]

    def get(self, request):
        tenant = request.tenant
        keys = BillingKey.objects.filter(tenant=tenant, is_active=True)
        serializer = BillingKeySerializer(keys, many=True)
        return Response(serializer.data)


class MyBillingProfileView(APIView):
    """
    GET/PATCH /api/v1/billing/profile/
    결제자 정보 조회/수정.
    """
    permission_classes = [IsAuthenticated, TenantResolvedAndOwner]

    def get(self, request):
        tenant = request.tenant
        try:
            profile = BillingProfile.objects.get(tenant=tenant)
        except BillingProfile.DoesNotExist:
            return Response({
                "id": None,
                "provider": "tosspayments",
                "payer_name": "",
                "payer_email": "",
                "payer_phone": "",
            })
        return Response(BillingProfileSerializer(profile).data)

    def patch(self, request):
        tenant = request.tenant
        profile, _ = BillingProfile.objects.get_or_create(tenant=tenant)
        serializer = BillingProfileSerializer(profile, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)


class CardRegisterPrepareView(APIView):
    """
    POST /api/v1/billing/card/register/prepare/
    카드 등록 준비 — Toss SDK v2 requestBillingAuth() 호출에 필요한 파라미터 반환.
    """
    permission_classes = [IsAuthenticated, TenantResolvedAndOwner]

    def post(self, request):
        from django.conf import settings as django_settings
        tenant = request.tenant
        customer_key = billing_key_service.get_or_create_customer_key(tenant.id)
        base_url = request.build_absolute_uri("/").rstrip("/")

        return Response({
            "clientKey": django_settings.TOSS_PAYMENTS_CLIENT_KEY,
            "customerKey": customer_key,
            "successUrl": f"{base_url}/admin/billing/card/callback?status=success",
            "failUrl": f"{base_url}/admin/billing/card/callback?status=fail",
        })


class CardRegisterCallbackView(APIView):
    """
    POST /api/v1/billing/card/register/callback/
    Toss redirect 후 authKey로 빌링키 발급.
    """
    permission_classes = [IsAuthenticated, TenantResolvedAndOwner]

    def post(self, request):
        auth_key = request.data.get("authKey")
        if not auth_key:
            return Response({"detail": "authKey is required"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            bk = billing_key_service.issue_billing_key(request.tenant.id, auth_key)
        except ValueError as e:
            return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)

        return Response({
            "id": bk.id,
            "card_company": bk.card_company,
            "card_number_masked": bk.card_number_masked,
            "is_active": bk.is_active,
            "message": "카드가 등록되었습니다.",
        })


class CardDeleteView(APIView):
    """
    DELETE /api/v1/billing/cards/{pk}/
    카드 삭제 (Toss 빌링키 삭제 + 로컬 비활성화).
    """
    permission_classes = [IsAuthenticated, TenantResolvedAndOwner]

    def delete(self, request, pk):
        # 테넌트 격리 확인
        try:
            bk = BillingKey.objects.get(pk=pk, tenant=request.tenant, is_active=True)
        except BillingKey.DoesNotExist:
            return Response({"detail": "Card not found"}, status=status.HTTP_404_NOT_FOUND)

        success = billing_key_service.delete_billing_key(bk.id)
        if not success:
            return Response(
                {"detail": "카드 삭제에 실패했습니다. 잠시 후 다시 시도해 주세요."},
                status=status.HTTP_502_BAD_GATEWAY,
            )
        return Response({"message": "카드가 삭제되었습니다."})


class CancelSubscriptionView(APIView):
    """
    POST /api/v1/billing/cancel/
    해지 예약.
    """
    permission_classes = [IsAuthenticated, TenantResolvedAndOwner]

    def post(self, request):
        program = request.tenant.program
        try:
            program = subscription_service.schedule_cancel(program.pk)
        except subscription_service.SubscriptionTransitionError as e:
            return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)

        return Response({
            "cancel_at_period_end": program.cancel_at_period_end,
            "canceled_at": str(program.canceled_at) if program.canceled_at else None,
            "subscription_expires_at": str(program.subscription_expires_at),
            "message": "해지가 예약되었습니다. 현재 구독 기간이 종료되면 자동으로 해지됩니다.",
        })


class RevokeCancelView(APIView):
    """
    POST /api/v1/billing/cancel/revoke/
    해지 예약 철회.
    """
    permission_classes = [IsAuthenticated, TenantResolvedAndOwner]

    def post(self, request):
        program = request.tenant.program
        program = subscription_service.revoke_cancel(program.pk)
        return Response({
            "cancel_at_period_end": program.cancel_at_period_end,
            "message": "해지 예약이 철회되었습니다.",
        })
