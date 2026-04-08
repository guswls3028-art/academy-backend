# PATH: apps/domains/fees/views.py

import logging
from datetime import date

from django.db.models import Q
from rest_framework.viewsets import ModelViewSet
from rest_framework.views import APIView
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated

from rest_framework.pagination import PageNumberPagination

from apps.core.permissions import TenantResolvedAndStaff, TenantResolvedAndMember, IsStudent


class FeesLargePagination(PageNumberPagination):
    """수납 도메인용 — 학원당 데이터가 수백건 수준이므로 넉넉하게."""
    page_size = 500
    page_size_query_param = "page_size"
    max_page_size = 2000

from .models import FeeTemplate, StudentFee, StudentInvoice, FeePayment


def _fees_enabled(request) -> bool:
    """테넌트의 fee_management feature flag 확인."""
    tenant = getattr(request, "tenant", None)
    if not tenant:
        return False
    try:
        flags = tenant.program.feature_flags or {}
        return bool(flags.get("fee_management"))
    except Exception:
        return False
from .serializers import (
    FeeTemplateSerializer,
    FeeTemplateCreateSerializer,
    StudentFeeSerializer,
    StudentFeeBulkAssignSerializer,
    StudentInvoiceListSerializer,
    StudentInvoiceDetailSerializer,
    GenerateInvoicesSerializer,
    FeePaymentSerializer,
    RecordPaymentSerializer,
)
from . import services

logger = logging.getLogger(__name__)


# ========================================================
# FeeTemplate (비목 관리)
# ========================================================

class FeeTemplateViewSet(ModelViewSet):
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]
    pagination_class = FeesLargePagination

    def get_serializer_class(self):
        if self.action in ("create", "update", "partial_update"):
            return FeeTemplateCreateSerializer
        return FeeTemplateSerializer

    def get_queryset(self):
        if not _fees_enabled(self.request):
            return FeeTemplate.objects.none()
        tenant = self.request.tenant
        qs = FeeTemplate.objects.filter(tenant=tenant).select_related("lecture")

        # 필터
        fee_type = self.request.query_params.get("fee_type")
        if fee_type:
            qs = qs.filter(fee_type=fee_type)

        is_active = self.request.query_params.get("is_active")
        if is_active is not None:
            qs = qs.filter(is_active=is_active.lower() == "true")

        lecture_id = self.request.query_params.get("lecture")
        if lecture_id:
            qs = qs.filter(lecture_id=lecture_id)

        return qs.order_by("-created_at")

    def perform_create(self, serializer):
        serializer.save(tenant=self.request.tenant)

    def perform_destroy(self, instance):
        # Soft deactivate instead of hard delete
        instance.is_active = False
        instance.save(update_fields=["is_active", "updated_at"])


# ========================================================
# StudentFee (학생별 비용 할당)
# ========================================================

class StudentFeeViewSet(ModelViewSet):
    serializer_class = StudentFeeSerializer
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]
    pagination_class = FeesLargePagination

    def get_queryset(self):
        if not _fees_enabled(self.request):
            return StudentFee.objects.none()
        tenant = self.request.tenant
        qs = (
            StudentFee.objects
            .filter(tenant=tenant)
            .select_related("student", "fee_template", "fee_template__lecture")
        )

        student_id = self.request.query_params.get("student")
        if student_id:
            qs = qs.filter(student_id=student_id)

        lecture_id = self.request.query_params.get("lecture")
        if lecture_id:
            qs = qs.filter(fee_template__lecture_id=lecture_id)

        is_active = self.request.query_params.get("is_active")
        if is_active is not None:
            qs = qs.filter(is_active=is_active.lower() == "true")

        return qs.order_by("student__name")

    def perform_create(self, serializer):
        serializer.save(tenant=self.request.tenant)

    @action(detail=False, methods=["post"], url_path="bulk-assign")
    def bulk_assign(self, request):
        """여러 학생에게 비목을 일괄 할당."""
        ser = StudentFeeBulkAssignSerializer(data=request.data)
        ser.is_valid(raise_exception=True)

        tenant = request.tenant
        student_ids = ser.validated_data["student_ids"]
        template_id = ser.validated_data["fee_template_id"]

        try:
            template = FeeTemplate.objects.get(id=template_id, tenant=tenant)
        except FeeTemplate.DoesNotExist:
            return Response({"detail": "비목을 찾을 수 없습니다."}, status=status.HTTP_404_NOT_FOUND)

        created = 0
        skipped = 0
        for sid in student_ids:
            _, was_created = StudentFee.objects.get_or_create(
                tenant=tenant,
                student_id=sid,
                fee_template=template,
                defaults={"is_active": True},
            )
            if was_created:
                created += 1
            else:
                skipped += 1

        return Response({
            "created": created,
            "skipped": skipped,
            "total": len(student_ids),
        })


# ========================================================
# StudentInvoice (청구서)
# ========================================================

class StudentInvoiceViewSet(ModelViewSet):
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]
    pagination_class = FeesLargePagination
    http_method_names = ["get", "patch", "delete", "post"]

    def get_serializer_class(self):
        if self.action == "retrieve":
            return StudentInvoiceDetailSerializer
        return StudentInvoiceListSerializer

    def get_queryset(self):
        if not _fees_enabled(self.request):
            return StudentInvoice.objects.none()
        tenant = self.request.tenant
        qs = (
            StudentInvoice.objects
            .filter(tenant=tenant)
            .select_related("student")
        )

        # 필터
        year = self.request.query_params.get("billing_year")
        month = self.request.query_params.get("billing_month")
        if year:
            qs = qs.filter(billing_year=int(year))
        if month:
            qs = qs.filter(billing_month=int(month))

        status_filter = self.request.query_params.get("status")
        if status_filter:
            qs = qs.filter(status=status_filter)

        student_id = self.request.query_params.get("student")
        if student_id:
            qs = qs.filter(student_id=student_id)

        search = self.request.query_params.get("search")
        if search:
            qs = qs.filter(
                Q(student__name__icontains=search) |
                Q(invoice_number__icontains=search)
            )

        # 강의 필터: 해당 강의의 비목이 포함된 청구서
        lecture_id = self.request.query_params.get("lecture")
        if lecture_id:
            qs = qs.filter(items__fee_template__lecture_id=lecture_id).distinct()

        # 비목 유형 필터
        fee_type = self.request.query_params.get("fee_type")
        if fee_type:
            qs = qs.filter(items__fee_template__fee_type=fee_type).distinct()

        # 정렬: 미납 우선 (OVERDUE → PENDING → PARTIAL → PAID)
        ordering = self.request.query_params.get("ordering", "")
        if ordering == "unpaid_first":
            from django.db.models import Case, When, Value, IntegerField
            qs = qs.annotate(
                status_order=Case(
                    When(status="OVERDUE", then=Value(0)),
                    When(status="PENDING", then=Value(1)),
                    When(status="PARTIAL", then=Value(2)),
                    When(status="PAID", then=Value(3)),
                    default=Value(4),
                    output_field=IntegerField(),
                ),
            ).order_by("status_order", "student__name")
        else:
            qs = qs.order_by("-billing_year", "-billing_month", "student__name")

        return qs

    def retrieve(self, request, *args, **kwargs):
        instance = self.get_object()
        # prefetch items and payments for detail view
        serializer = self.get_serializer(instance)
        return Response(serializer.data)

    def perform_update(self, serializer):
        # Only allow updating memo and due_date
        serializer.save()

    def perform_destroy(self, instance):
        """청구서 취소 (DELETE)."""
        services.cancel_invoice(self.request.tenant, instance.id)

    @action(detail=False, methods=["post"])
    def generate(self, request):
        """월 청구서 일괄 생성."""
        ser = GenerateInvoicesSerializer(data=request.data)
        ser.is_valid(raise_exception=True)

        result = services.generate_monthly_invoices(
            tenant=request.tenant,
            billing_year=ser.validated_data["billing_year"],
            billing_month=ser.validated_data["billing_month"],
            due_date=ser.validated_data["due_date"],
            created_by=request.user,
        )

        return Response(result)


# ========================================================
# FeePayment (수납 기록)
# ========================================================

class FeePaymentViewSet(ModelViewSet):
    serializer_class = FeePaymentSerializer
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]
    pagination_class = FeesLargePagination
    http_method_names = ["get", "post"]

    def get_queryset(self):
        if not _fees_enabled(self.request):
            return FeePayment.objects.none()
        tenant = self.request.tenant
        qs = (
            FeePayment.objects
            .filter(tenant=tenant)
            .select_related("student", "invoice")
        )

        invoice_id = self.request.query_params.get("invoice")
        if invoice_id:
            qs = qs.filter(invoice_id=invoice_id)

        student_id = self.request.query_params.get("student")
        if student_id:
            qs = qs.filter(student_id=student_id)

        return qs.order_by("-paid_at")

    def create(self, request, *args, **kwargs):
        """수납 기록 생성."""
        ser = RecordPaymentSerializer(data=request.data)
        ser.is_valid(raise_exception=True)

        try:
            payment = services.record_payment(
                tenant=request.tenant,
                invoice_id=ser.validated_data["invoice_id"],
                amount=ser.validated_data["amount"],
                payment_method=ser.validated_data["payment_method"],
                paid_at=ser.validated_data.get("paid_at"),
                recorded_by=request.user,
                receipt_note=ser.validated_data.get("receipt_note", ""),
                memo=ser.validated_data.get("memo", ""),
            )
        except ValueError as e:
            return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)
        except StudentInvoice.DoesNotExist:
            return Response({"detail": "청구서를 찾을 수 없습니다."}, status=status.HTTP_404_NOT_FOUND)

        return Response(
            FeePaymentSerializer(payment).data,
            status=status.HTTP_201_CREATED,
        )

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        """수납 취소."""
        try:
            payment = services.cancel_payment(request.tenant, int(pk))
        except ValueError as e:
            return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)
        except FeePayment.DoesNotExist:
            return Response({"detail": "수납 기록을 찾을 수 없습니다."}, status=status.HTTP_404_NOT_FOUND)

        return Response(FeePaymentSerializer(payment).data)


# ========================================================
# Dashboard (수납 현황)
# ========================================================

class FeeDashboardView(APIView):
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    def get(self, request):
        today = date.today()
        year = int(request.query_params.get("year", today.year))
        month = int(request.query_params.get("month", today.month))

        stats = services.get_dashboard_stats(request.tenant, year, month)
        return Response(stats)


class FeeOverdueView(APIView):
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    def get(self, request):
        tenant = request.tenant
        overdue = (
            StudentInvoice.objects
            .filter(tenant=tenant, status="OVERDUE")
            .select_related("student")
            .order_by("due_date")
        )
        data = StudentInvoiceListSerializer(overdue, many=True).data
        return Response(data)


# ========================================================
# Student API (학생 조회 전용)
# ========================================================

class StudentFeeInvoiceListView(APIView):
    """학생 본인의 청구서 목록."""
    permission_classes = [IsAuthenticated, TenantResolvedAndMember]

    def get(self, request):
        tenant = request.tenant
        student = getattr(request.user, "student_profile", None)
        if not student:
            return Response({"detail": "학생 계정이 필요합니다."}, status=status.HTTP_403_FORBIDDEN)

        invoices = (
            StudentInvoice.objects
            .filter(tenant=tenant, student=student)
            .exclude(status="CANCELLED")
            .select_related("student")
            .order_by("-billing_year", "-billing_month")
        )

        data = StudentInvoiceListSerializer(invoices, many=True).data
        return Response(data)


class StudentFeeInvoiceDetailView(APIView):
    """학생 본인의 청구서 상세."""
    permission_classes = [IsAuthenticated, TenantResolvedAndMember]

    def get(self, request, pk):
        tenant = request.tenant
        student = getattr(request.user, "student_profile", None)
        if not student:
            return Response({"detail": "학생 계정이 필요합니다."}, status=status.HTTP_403_FORBIDDEN)

        try:
            invoice = (
                StudentInvoice.objects
                .filter(tenant=tenant, student=student)
                .exclude(status="CANCELLED")
                .prefetch_related("items", "payments")
                .get(pk=pk)
            )
        except StudentInvoice.DoesNotExist:
            return Response({"detail": "청구서를 찾을 수 없습니다."}, status=status.HTTP_404_NOT_FOUND)

        data = StudentInvoiceDetailSerializer(invoice).data
        return Response(data)


class StudentFeePaymentListView(APIView):
    """학생 본인의 납부 내역."""
    permission_classes = [IsAuthenticated, TenantResolvedAndMember]

    def get(self, request):
        tenant = request.tenant
        student = getattr(request.user, "student_profile", None)
        if not student:
            return Response({"detail": "학생 계정이 필요합니다."}, status=status.HTTP_403_FORBIDDEN)

        payments = (
            FeePayment.objects
            .filter(tenant=tenant, student=student, status="SUCCESS")
            .select_related("invoice", "student")
            .order_by("-paid_at")
        )

        data = FeePaymentSerializer(payments, many=True).data
        return Response(data)
