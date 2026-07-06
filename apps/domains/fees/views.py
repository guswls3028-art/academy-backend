# PATH: apps/domains/fees/views.py

import logging
from django.db.models import Q
from django.utils import timezone
from rest_framework.viewsets import ModelViewSet
from rest_framework.views import APIView
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.exceptions import PermissionDenied, ValidationError

from rest_framework.pagination import PageNumberPagination

from apps.core.permissions import TenantResolvedAndMember
from apps.support.fees.view_dependencies import active_student_ids_for_tenant, get_request_student


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
    StudentInvoiceUpdateSerializer,
    GenerateInvoicesSerializer,
    FeePaymentSerializer,
    RecordPaymentSerializer,
)
from . import services

logger = logging.getLogger(__name__)


class TenantResolvedAndFeeManager(BasePermission):
    """수납 관리는 원장/관리자만 접근한다."""

    message = "Fee manager membership required."

    def has_permission(self, request, view):
        tenant = getattr(request, "tenant", None)
        user = getattr(request, "user", None)
        if not tenant or not user or not user.is_authenticated:
            return False
        from academy.adapters.db.django import repositories_core as core_repo

        if core_repo.membership_exists_staff(
            tenant=tenant,
            user=user,
            staff_roles=("owner", "admin"),
        ):
            return True
        return bool(user.is_superuser and getattr(user, "tenant_id", None) == tenant.id)


class FeeManagementEnabledMixin:
    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        if not _fees_enabled(request):
            raise PermissionDenied("수납 기능이 활성화되어 있지 않습니다.")


def _validate_fee_template_lecture_tenant(*, tenant, lecture):
    if lecture is None:
        return
    if lecture.tenant_id != tenant.id:
        raise ValidationError({"lecture": "다른 테넌트의 강의는 연결할 수 없습니다."})


def _validate_student_fee_tenant_consistency(*, tenant, student, fee_template, enrollment):
    if student.tenant_id != tenant.id:
        raise ValidationError({"student": "다른 테넌트의 학생은 지정할 수 없습니다."})
    if fee_template.tenant_id != tenant.id:
        raise ValidationError({"fee_template": "다른 테넌트의 비목은 지정할 수 없습니다."})
    if enrollment is not None and enrollment.tenant_id != tenant.id:
        raise ValidationError({"enrollment": "다른 테넌트의 수강정보는 지정할 수 없습니다."})
    if enrollment is not None and enrollment.student_id != student.id:
        raise ValidationError({"enrollment": "학생과 수강정보가 일치하지 않습니다."})
    if (
        enrollment is not None
        and fee_template.lecture_id is not None
        and enrollment.lecture_id != fee_template.lecture_id
    ):
        raise ValidationError({"enrollment": "비목의 강의와 수강정보의 강의가 일치하지 않습니다."})


# ========================================================
# FeeTemplate (비목 관리)
# ========================================================

class FeeTemplateViewSet(FeeManagementEnabledMixin, ModelViewSet):
    permission_classes = [IsAuthenticated, TenantResolvedAndFeeManager]
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
        lecture = serializer.validated_data.get("lecture")
        _validate_fee_template_lecture_tenant(tenant=self.request.tenant, lecture=lecture)
        serializer.save(tenant=self.request.tenant)

    def perform_update(self, serializer):
        lecture = serializer.validated_data.get("lecture", serializer.instance.lecture)
        _validate_fee_template_lecture_tenant(tenant=self.request.tenant, lecture=lecture)
        serializer.save()

    def perform_destroy(self, instance):
        # Soft deactivate instead of hard delete
        instance.is_active = False
        instance.save(update_fields=["is_active", "updated_at"])


# ========================================================
# StudentFee (학생별 비용 할당)
# ========================================================

class StudentFeeViewSet(FeeManagementEnabledMixin, ModelViewSet):
    serializer_class = StudentFeeSerializer
    permission_classes = [IsAuthenticated, TenantResolvedAndFeeManager]
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
        _validate_student_fee_tenant_consistency(
            tenant=self.request.tenant,
            student=serializer.validated_data["student"],
            fee_template=serializer.validated_data["fee_template"],
            enrollment=serializer.validated_data.get("enrollment"),
        )
        serializer.save(tenant=self.request.tenant)

    def perform_update(self, serializer):
        instance = serializer.instance
        _validate_student_fee_tenant_consistency(
            tenant=self.request.tenant,
            student=serializer.validated_data.get("student", instance.student),
            fee_template=serializer.validated_data.get("fee_template", instance.fee_template),
            enrollment=serializer.validated_data.get("enrollment", instance.enrollment),
        )
        serializer.save()

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

        # 학생 ID가 해당 테넌트 소속인지 검증 (크로스 테넌트 방지)
        valid_student_ids = active_student_ids_for_tenant(
            tenant=tenant,
            student_ids=student_ids,
        )
        invalid_ids = set(student_ids) - valid_student_ids
        if invalid_ids:
            return Response(
                {"detail": f"해당 학원 소속이 아닌 학생이 포함되어 있습니다: {sorted(invalid_ids)[:5]}"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        created = 0
        skipped = 0
        for sid in valid_student_ids:
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

class StudentInvoiceViewSet(FeeManagementEnabledMixin, ModelViewSet):
    permission_classes = [IsAuthenticated, TenantResolvedAndFeeManager]
    pagination_class = FeesLargePagination
    http_method_names = ["get", "patch", "delete", "post"]

    def get_serializer_class(self):
        if self.action in ("update", "partial_update"):
            return StudentInvoiceUpdateSerializer
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
        serializer.save()

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop("partial", False)
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        self.perform_update(serializer)
        instance.refresh_from_db()
        return Response(StudentInvoiceDetailSerializer(instance).data)

    def destroy(self, request, *args, **kwargs):
        """청구서 취소 (DELETE)."""
        instance = self.get_object()
        try:
            services.cancel_invoice(request.tenant, instance.id)
        except ValueError as e:
            return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(status=status.HTTP_204_NO_CONTENT)

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

class FeePaymentViewSet(FeeManagementEnabledMixin, ModelViewSet):
    serializer_class = FeePaymentSerializer
    permission_classes = [IsAuthenticated, TenantResolvedAndFeeManager]
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
        idempotency_key = (
            ser.validated_data.get("idempotency_key")
            or request.headers.get("Idempotency-Key")
            or request.headers.get("X-Idempotency-Key")
            or ""
        )
        if len(idempotency_key) > 100:
            raise ValidationError({"idempotency_key": "멱등성 키는 100자 이하여야 합니다."})

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
                idempotency_key=idempotency_key,
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

class FeeDashboardView(FeeManagementEnabledMixin, APIView):
    permission_classes = [IsAuthenticated, TenantResolvedAndFeeManager]

    def get(self, request):
        today = timezone.localdate()
        year = int(request.query_params.get("year", today.year))
        month = int(request.query_params.get("month", today.month))

        stats = services.get_dashboard_stats(request.tenant, year, month)
        return Response(stats)


class FeeOverdueView(FeeManagementEnabledMixin, APIView):
    permission_classes = [IsAuthenticated, TenantResolvedAndFeeManager]

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

def _resolve_student_or_children(request, tenant):
    """
    학생: 본인 student.
    학부모: 선택된 자녀(X-Student-Id), 없으면 기본 자녀.
    어느 쪽도 아니면 None.
    """
    student = get_request_student(request)
    if student and student.tenant_id == tenant.id:
        return [student]
    return None


class StudentFeeInvoiceListView(APIView):
    """학생 본인 또는 학부모(자녀)의 청구서 목록."""
    permission_classes = [IsAuthenticated, TenantResolvedAndMember]

    def get(self, request):
        tenant = request.tenant
        students = _resolve_student_or_children(request, tenant)
        if not students:
            return Response({"detail": "학생 또는 학부모 계정이 필요합니다."}, status=status.HTTP_403_FORBIDDEN)

        invoices = (
            StudentInvoice.objects
            .filter(tenant=tenant, student__in=students)
            .exclude(status="CANCELLED")
            .select_related("student")
            .order_by("-billing_year", "-billing_month")
        )

        data = StudentInvoiceListSerializer(invoices, many=True).data
        return Response(data)


class StudentFeeInvoiceDetailView(APIView):
    """학생 본인 또는 학부모(자녀)의 청구서 상세."""
    permission_classes = [IsAuthenticated, TenantResolvedAndMember]

    def get(self, request, pk):
        tenant = request.tenant
        students = _resolve_student_or_children(request, tenant)
        if not students:
            return Response({"detail": "학생 또는 학부모 계정이 필요합니다."}, status=status.HTTP_403_FORBIDDEN)

        try:
            invoice = (
                StudentInvoice.objects
                .filter(tenant=tenant, student__in=students)
                .exclude(status="CANCELLED")
                .prefetch_related("items", "payments")
                .get(pk=pk)
            )
        except StudentInvoice.DoesNotExist:
            return Response({"detail": "청구서를 찾을 수 없습니다."}, status=status.HTTP_404_NOT_FOUND)

        data = StudentInvoiceDetailSerializer(invoice).data
        return Response(data)


class StudentFeePaymentListView(APIView):
    """학생 본인 또는 학부모(자녀)의 납부 내역."""
    permission_classes = [IsAuthenticated, TenantResolvedAndMember]

    def get(self, request):
        tenant = request.tenant
        students = _resolve_student_or_children(request, tenant)
        if not students:
            return Response({"detail": "학생 또는 학부모 계정이 필요합니다."}, status=status.HTTP_403_FORBIDDEN)

        payments = (
            FeePayment.objects
            .filter(tenant=tenant, student__in=students, status="SUCCESS")
            .select_related("invoice", "student")
            .order_by("-paid_at")
        )

        data = FeePaymentSerializer(payments, many=True).data
        return Response(data)
