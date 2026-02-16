# PATH: apps/domains/staffs/views.py
from io import BytesIO

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone
from django.http import HttpResponse
from django.contrib.auth import get_user_model

from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.filters import SearchFilter, OrderingFilter
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated, BasePermission
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.viewsets import ReadOnlyModelViewSet

from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table
from reportlab.lib.styles import getSampleStyleSheet

from .models import (
    Staff,
    WorkType,
    StaffWorkType,
    WorkRecord,
    ExpenseRecord,
    WorkMonthLock,
    PayrollSnapshot,
)
from .serializers import (
    WorkTypeSerializer,
    StaffWorkTypeSerializer,
    StaffListSerializer,
    StaffDetailSerializer,
    StaffCreateUpdateSerializer,
    WorkRecordSerializer,
    ExpenseRecordSerializer,
    WorkMonthLockSerializer,
    PayrollSnapshotSerializer,
)
from academy.adapters.db.django import repositories_staffs as staff_repo
from .filters import StaffFilter, WorkRecordFilter, ExpenseRecordFilter
from apps.domains.teachers.models import Teacher

User = get_user_model()

# ===========================
# Permissions
# ===========================

class IsPayrollManager(BasePermission):
    """급여/근무 관리 권한. owner(원장)=마스터키 통과, 그 외 superuser/staff/직원 is_manager."""
    def has_permission(self, request, view):
        user = request.user
        if not user or not user.is_authenticated:
            return False
        if user.is_superuser or user.is_staff:
            return True
        tenant = getattr(request, "tenant", None)
        if tenant:
            from academy.adapters.db.django import repositories_core as core_repo
            if core_repo.membership_exists_staff(tenant=tenant, user=user, staff_roles=("owner",)):
                return True
        return getattr(getattr(user, "staff_profile", None), "is_manager", False)

# ===========================
# Helpers
# ===========================

def is_month_locked(staff, date):
    return staff_repo.is_month_locked(staff, date.year, date.month)


def can_manage_payroll(user, tenant=None) -> bool:
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser or user.is_staff:
        return True
    if tenant:
        from academy.adapters.db.django import repositories_core as core_repo
        if core_repo.membership_exists_staff(tenant=tenant, user=user, staff_roles=("owner",)):
            return True
    return getattr(getattr(user, "staff_profile", None), "is_manager", False)


def generate_payroll_snapshot(staff, year, month, user):
    if staff_repo.payroll_snapshot_exists_staff(staff, year, month):
        raise ValidationError("이미 급여 스냅샷이 생성된 월입니다.")

    with transaction.atomic():
        wr_qs = staff_repo.work_record_queryset_staff_date_ym(staff, year, month)
        er_qs = staff_repo.expense_record_queryset_staff_date_ym(staff, year, month, status="APPROVED")

        work_hours = wr_qs.aggregate(total=Sum("work_hours"))["total"] or 0
        work_amount = wr_qs.aggregate(total=Sum("amount"))["total"] or 0
        approved_expense_amount = er_qs.aggregate(total=Sum("amount"))["total"] or 0
        total_amount = work_amount + approved_expense_amount

        staff_repo.payroll_snapshot_create_full(
            tenant=staff.tenant,
            staff=staff,
            year=year,
            month=month,
            work_hours=work_hours,
            work_amount=work_amount,
            approved_expense_amount=approved_expense_amount,
            total_amount=total_amount,
            generated_by=user,
        )

# ===========================
# WorkType
# ===========================

class WorkTypeViewSet(viewsets.ModelViewSet):
    serializer_class = WorkTypeSerializer
    permission_classes = [IsAuthenticated, IsPayrollManager]

    filter_backends = (DjangoFilterBackend, SearchFilter, OrderingFilter)
    filterset_fields = ["is_active"]
    search_fields = ["name", "description"]
    ordering_fields = ["name", "base_hourly_wage", "created_at"]

    def get_queryset(self):
        return staff_repo.work_type_queryset_tenant(self.request.tenant)

    def perform_create(self, serializer):
        serializer.save(tenant=self.request.tenant)

# ===========================
# Staff
# ===========================

class StaffViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated, IsPayrollManager]

    filter_backends = (DjangoFilterBackend, SearchFilter, OrderingFilter)
    filterset_class = StaffFilter
    search_fields = ["name", "phone"]
    ordering_fields = ["name", "created_at", "is_active"]

    def get_queryset(self):
        return staff_repo.staff_queryset_tenant(self.request.tenant)

    def get_serializer_class(self):
        if self.action == "list":
            return StaffListSerializer
        if self.action == "retrieve":
            return StaffDetailSerializer
        return StaffCreateUpdateSerializer

    def perform_create(self, serializer):
        serializer.save(tenant=self.request.tenant)

    def perform_destroy(self, instance):
        serializer = self.get_serializer(instance)
        serializer.delete(instance)

    @action(detail=False, methods=["get"], url_path="me", permission_classes=[IsAuthenticated])
    def me(self, request):
        return Response(
            {
                "is_authenticated": True,
                "is_superuser": bool(request.user.is_superuser),
                "is_staff": bool(request.user.is_staff),
                "is_payroll_manager": can_manage_payroll(request.user, getattr(request, "tenant", None)),
            }
        )

    # ===========================
    # 실시간 근무 (Staff 기준)
    # ===========================

    @action(detail=True, methods=["get"], url_path="work-records/current")
    def work_current(self, request, pk=None):
        staff = self.get_object()

        record = (
            WorkRecord.objects
            .filter(staff=staff, tenant=staff.tenant, end_time__isnull=True)
            .order_by("-start_time")
            .first()
        )

        if not record:
            return Response({"status": "OFF"})

        if record.current_break_started_at:
            return Response({
                "status": "BREAK",
                "work_record_id": record.id,
                "started_at": record.start_time,
                "break_started_at": record.current_break_started_at,
            })

        return Response({
            "status": "WORKING",
            "work_record_id": record.id,
            "started_at": record.start_time,
            "break_minutes": record.break_minutes,
        })

    @action(detail=True, methods=["post"], url_path="work-records/start-work")
    def start_work(self, request, pk=None):
        staff = self.get_object()
        now = timezone.now()

        if is_month_locked(staff, now.date()):
            raise ValidationError("마감된 월입니다.")

        if staff_repo.work_record_filter_open(staff).exists():
            raise ValidationError("이미 근무 중입니다.")

        work_type_id = request.data.get("work_type")
        if not work_type_id:
            raise ValidationError("work_type은 필수입니다.")

        record = staff_repo.work_record_create_start(
            staff=staff,
            work_type_id=work_type_id,
            date=now.date(),
            start_time=now.time(),
        )

        return Response(WorkRecordSerializer(record).data, status=201)

# ===========================
# StaffWorkType
# ===========================

class StaffWorkTypeViewSet(viewsets.ModelViewSet):
    serializer_class = StaffWorkTypeSerializer
    permission_classes = [IsAuthenticated, IsPayrollManager]

    filter_backends = (DjangoFilterBackend, OrderingFilter)
    filterset_fields = ["staff", "work_type"]
    ordering_fields = ["created_at"]

    def get_queryset(self):
        return staff_repo.staff_work_type_queryset_tenant(self.request.tenant)

    def perform_create(self, serializer):
        serializer.save(tenant=self.request.tenant)

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

    def perform_update(self, serializer):
        instance = self.get_object()

        if is_month_locked(instance.staff, instance.date):
            raise ValidationError("마감된 월입니다.")

        if instance.status == "APPROVED":
            raise ValidationError("승인된 비용은 수정할 수 없습니다.")

        new_status = serializer.validated_data.get("status", instance.status)

        if new_status != instance.status:
            if not can_manage_payroll(self.request.user):
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

# ===========================
# WorkMonthLock
# ===========================

class WorkMonthLockViewSet(viewsets.ModelViewSet):
    serializer_class = WorkMonthLockSerializer
    permission_classes = [IsAuthenticated, IsPayrollManager]

    def get_queryset(self):
        return staff_repo.work_month_lock_queryset_tenant(self.request.tenant)

    def create(self, request, *args, **kwargs):
        staff = staff_repo.staff_get(request.tenant, request.data.get("staff"))
        year = int(request.data.get("year"))
        month = int(request.data.get("month"))

        obj, _ = staff_repo.work_month_lock_update_or_create_defaults(
            request.tenant,
            staff,
            year,
            month,
            defaults={"is_locked": True, "locked_by": request.user},
        )

        generate_payroll_snapshot(
            staff=staff,
            year=year,
            month=month,
            user=request.user,
        )

        return Response(
            WorkMonthLockSerializer(obj).data,
            status=status.HTTP_201_CREATED,
        )

# ===========================
# PayrollSnapshot (ReadOnly + Export)
# ===========================

class PayrollSnapshotViewSet(ReadOnlyModelViewSet):
    serializer_class = PayrollSnapshotSerializer
    permission_classes = [IsAuthenticated, IsPayrollManager]

    def get_queryset(self):
        return staff_repo.payroll_snapshot_queryset_tenant(self.request.tenant)

    @action(detail=False, methods=["post"], url_path="export-excel")
    def export_excel(self, request):
        """급여 엑셀 내보내기(워커 비동기). POST body: { "year", "month" } → job_id 반환. GET /api/v1/jobs/<job_id>/ 폴링 후 result.download_url 로 다운로드."""
        year = request.data.get("year") or request.query_params.get("year")
        month = request.data.get("month") or request.query_params.get("month")

        if not year or not month:
            raise ValidationError("year, month는 필수입니다.")

        tenant = getattr(request, "tenant", None)
        if not tenant:
            return Response(
                {"detail": "tenant가 필요합니다."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        from apps.domains.ai.gateway import dispatch_job

        out = dispatch_job(
            job_type="staff_excel_export",
            payload={
                "tenant_id": str(tenant.id),
                "year": int(year),
                "month": int(month),
            },
            tenant_id=str(tenant.id),
            source_domain="staffs",
            source_id=f"{year}-{month}",
            tier="basic",
            idempotency_key=f"staff_export:{tenant.id}:{year}:{month}",
        )
        if not out.get("ok"):
            return Response(
                {"detail": out.get("error", "job 등록 실패")},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(
            {"job_id": out["job_id"], "status": "PENDING"},
            status=status.HTTP_202_ACCEPTED,
        )

    @action(detail=False, methods=["get"], url_path="export-pdf")
    def export_pdf(self, request):
        staff_id = request.query_params.get("staff")
        year = request.query_params.get("year")
        month = request.query_params.get("month")

        if not staff_id or not year or not month:
            raise ValidationError("staff, year, month는 필수입니다.")

        snap = self.get_queryset().filter(
            staff_id=staff_id,
            year=year,
            month=month,
        ).first()

        if not snap:
            return Response({"detail": "급여 스냅샷 없음"}, status=404)

        buffer = BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=A4)
        styles = getSampleStyleSheet()
        story = []

        story.append(Paragraph("급여 명세서", styles["Title"]))
        story.append(Spacer(1, 12))

        meta = [
            ["직원명", snap.staff.name],
            ["정산월", f"{snap.year}-{snap.month:02d}"],
            ["확정자", getattr(snap.generated_by, "username", "-") if snap.generated_by else "-"],
        ]
        story.append(Table(meta, colWidths=[120, 360]))
        story.append(Spacer(1, 16))

        rows = [
            ["근무시간", f"{snap.work_hours} h"],
            ["급여", f"{snap.work_amount:,} 원"],
            ["승인 비용", f"{snap.approved_expense_amount:,} 원"],
            ["총 지급액", f"{snap.total_amount:,} 원"],
        ]
        story.append(Table(rows, colWidths=[120, 360]))

        doc.build(story)
        pdf = buffer.getvalue()
        buffer.close()

        response = HttpResponse(content_type="application/pdf")
        response["Content-Disposition"] = (
            f'attachment; filename="payroll_{snap.staff.id}_{snap.year}_{snap.month:02d}.pdf"'
        )
        response.write(pdf)
        return response

# ===========================
# WorkRecord (Record 기준: 휴게/종료만)
# ===========================

class WorkRecordViewSet(viewsets.ModelViewSet):
    serializer_class = WorkRecordSerializer
    permission_classes = [IsAuthenticated, IsPayrollManager]

    filter_backends = (DjangoFilterBackend, OrderingFilter)
    filterset_class = WorkRecordFilter
    ordering_fields = ["date", "created_at", "amount"]

    def get_queryset(self):
        return (
            WorkRecord.objects
            .filter(tenant=self.request.tenant)
            .select_related("staff", "work_type")
            .order_by("-date", "-start_time")
        )

    @action(detail=True, methods=["post"])
    def start_break(self, request, pk=None):
        record = self.get_object()

        if is_month_locked(record.staff, record.date):
            raise ValidationError("마감된 월입니다.")

        if record.current_break_started_at:
            raise ValidationError("이미 휴게 중입니다.")

        record.current_break_started_at = timezone.now()
        record.save(update_fields=["current_break_started_at"])

        return Response({"status": "BREAK_STARTED"})

    @action(detail=True, methods=["post"])
    def end_break(self, request, pk=None):
        record = self.get_object()

        if is_month_locked(record.staff, record.date):
            raise ValidationError("마감된 월입니다.")

        if not record.current_break_started_at:
            raise ValidationError("휴게 중이 아닙니다.")

        now = timezone.now()
        delta = now - record.current_break_started_at
        record.break_minutes += int(delta.total_seconds() / 60)
        record.current_break_started_at = None
        record.save(update_fields=["break_minutes", "current_break_started_at"])

        return Response({"status": "BREAK_ENDED"})

    @action(detail=True, methods=["post"])
    def end_work(self, request, pk=None):
        record = self.get_object()

        if is_month_locked(record.staff, record.date):
            raise ValidationError("마감된 월입니다.")

        if record.end_time:
            raise ValidationError("이미 종료된 근무입니다.")

        if record.current_break_started_at:
            now = timezone.now()
            delta = now - record.current_break_started_at
            record.break_minutes += int(delta.total_seconds() / 60)
            record.current_break_started_at = None

        record.end_time = timezone.now().time()
        record.save()

        return Response(WorkRecordSerializer(record).data)
