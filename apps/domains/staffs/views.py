# PATH: apps/domains/staffs/views.py
from io import BytesIO
from django.db.models import Sum
from django.utils import timezone
from django.http import HttpResponse
from django.db import transaction
from django.contrib.auth import get_user_model

from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.filters import SearchFilter, OrderingFilter
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated, BasePermission
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.viewsets import ReadOnlyModelViewSet

from openpyxl import Workbook
from openpyxl.styles import Font, Alignment

from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib import colors

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
from .filters import StaffFilter, WorkRecordFilter, ExpenseRecordFilter
from apps.domains.teachers.models import Teacher

User = get_user_model()

# ===========================
# Permissions
# ===========================

class IsPayrollManager(BasePermission):
    """
    superuser OR staff OR staff_profile.is_manager
    """

    def has_permission(self, request, view):
        user = request.user
        if not user or not user.is_authenticated:
            return False

        if user.is_superuser or user.is_staff:
            return True

        return getattr(getattr(user, "staff_profile", None), "is_manager", False)


# ===========================
# Helper
# ===========================

def is_month_locked(staff, date):
    return WorkMonthLock.objects.filter(
        staff=staff,
        year=date.year,
        month=date.month,
        is_locked=True,
    ).exists()


def generate_payroll_snapshot(staff, year, month, user):
    """
    월 마감 시 1회 생성되는 급여 스냅샷 (불변)
    """
    if PayrollSnapshot.objects.filter(
        staff=staff, year=year, month=month
    ).exists():
        return

    wr_qs = WorkRecord.objects.filter(
        staff=staff,
        date__year=year,
        date__month=month,
    )

    er_qs = ExpenseRecord.objects.filter(
        staff=staff,
        date__year=year,
        date__month=month,
        status="APPROVED",
    )

    work_hours = wr_qs.aggregate(total=Sum("work_hours"))["total"] or 0
    work_amount = wr_qs.aggregate(total=Sum("amount"))["total"] or 0
    approved_expense_amount = er_qs.aggregate(total=Sum("amount"))["total"] or 0

    PayrollSnapshot.objects.create(
        staff=staff,
        year=year,
        month=month,
        work_hours=work_hours,
        work_amount=work_amount,
        approved_expense_amount=approved_expense_amount,
        total_amount=work_amount + approved_expense_amount,
        generated_by=user,
    )


def can_manage_payroll(user) -> bool:
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser or user.is_staff:
        return True
    return getattr(getattr(user, "staff_profile", None), "is_manager", False)


# ===========================
# WorkType
# ===========================

class WorkTypeViewSet(viewsets.ModelViewSet):
    queryset = WorkType.objects.all().order_by("name")
    serializer_class = WorkTypeSerializer
    permission_classes = [IsAuthenticated]

    filter_backends = (DjangoFilterBackend, SearchFilter, OrderingFilter)
    filterset_fields = ["is_active"]
    search_fields = ["name", "description"]
    ordering_fields = ["name", "base_hourly_wage", "created_at"]


# ===========================
# Staff
# ===========================

class StaffViewSet(viewsets.ModelViewSet):
    queryset = (
        Staff.objects.all()
        .select_related("user")
        .prefetch_related("staff_work_types__work_type")
        .order_by("name")
    )
    permission_classes = [IsAuthenticated]

    filter_backends = (DjangoFilterBackend, SearchFilter, OrderingFilter)
    filterset_class = StaffFilter
    search_fields = ["name", "phone"]
    ordering_fields = ["name", "created_at", "is_active"]

    def get_serializer_class(self):
        if self.action == "list":
            return StaffListSerializer
        if self.action == "retrieve":
            return StaffDetailSerializer
        return StaffCreateUpdateSerializer

    # 🔥 CHANGED: Staff 삭제 시 Serializer.delete() 위임
    def perform_destroy(self, instance):
        serializer = self.get_serializer(instance)
        serializer.delete(instance)

    @action(detail=False, methods=["get"], url_path="me")
    def me(self, request):
        """
        프론트 UX 분리를 위한 권한 정보
        """
        return Response(
            {
                "is_authenticated": bool(request.user and request.user.is_authenticated),
                "is_superuser": bool(getattr(request.user, "is_superuser", False)),
                "is_staff": bool(getattr(request.user, "is_staff", False)),
                "is_payroll_manager": can_manage_payroll(request.user),
            }
        )

    # ===========================
    # CREATE (User + Staff + Teacher)
    # ===========================
    def create(self, request, *args, **kwargs):
        data = request.data

        username = data.get("username")
        password = data.get("password")
        role = data.get("role")  # TEACHER | ASSISTANT

        if not username or not password or not role:
            raise ValidationError("username, password, role 은 필수입니다.")

        if role not in ("TEACHER", "ASSISTANT"):
            raise ValidationError("role 은 TEACHER 또는 ASSISTANT 여야 합니다.")

        if User.objects.filter(username=username).exists():
            raise ValidationError("이미 존재하는 username 입니다.")

        with transaction.atomic():
            user = User.objects.create(
                username=username,
                name=data.get("name", ""),
                phone=data.get("phone", ""),
                is_staff=(role == "TEACHER"),
            )
            user.set_password(password)
            user.save()

            staff = Staff.objects.create(
                user=user,
                name=data.get("name", ""),
                phone=data.get("phone", ""),
                is_active=True,
                is_manager=False,
                pay_type="MONTHLY" if role == "TEACHER" else "HOURLY",
            )

            if role == "TEACHER":
                Teacher.objects.create(
                    name=staff.name,
                    phone=staff.phone,
                    is_active=True,
                )

        return Response(
            StaffDetailSerializer(staff).data,
            status=status.HTTP_201_CREATED,
        )

    @action(detail=True, methods=["get", "post"], url_path="work-types")
    def work_types(self, request, pk=None):
        staff = self.get_object()

        if request.method.lower() == "get":
            qs = staff.staff_work_types.select_related("work_type").all()
            return Response(StaffWorkTypeSerializer(qs, many=True).data)

        serializer = StaffWorkTypeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        StaffWorkType.objects.create(
            staff=staff,
            work_type=serializer.validated_data["work_type"],
            hourly_wage=serializer.validated_data.get("hourly_wage"),
        )

        qs = staff.staff_work_types.select_related("work_type").all()
        return Response(
            StaffWorkTypeSerializer(qs, many=True).data,
            status=status.HTTP_201_CREATED,
        )

    @action(detail=True, methods=["get"], url_path="summary")
    def summary(self, request, pk=None):
        staff = self.get_object()
        date_from = request.query_params.get("date_from")
        date_to = request.query_params.get("date_to")

        wr_qs = staff.work_records.all()
        er_qs = staff.expense_records.all()

        if date_from:
            wr_qs = wr_qs.filter(date__gte=date_from)
            er_qs = er_qs.filter(date__gte=date_from)
        if date_to:
            wr_qs = wr_qs.filter(date__lte=date_to)
            er_qs = er_qs.filter(date__lte=date_to)

        return Response(
            {
                "staff_id": staff.id,
                "work_hours": wr_qs.aggregate(total=Sum("work_hours"))["total"] or 0,
                "work_amount": wr_qs.aggregate(total=Sum("amount"))["total"] or 0,
                "expense_amount": er_qs.aggregate(total=Sum("amount"))["total"] or 0,
                "total_amount": (
                    (wr_qs.aggregate(total=Sum("amount"))["total"] or 0)
                    + (er_qs.aggregate(total=Sum("amount"))["total"] or 0)
                ),
            }
        )

# ===========================
# StaffWorkType
# ===========================
class StaffWorkTypeViewSet(viewsets.ModelViewSet):
    queryset = StaffWorkType.objects.select_related("staff", "work_type")
    serializer_class = StaffWorkTypeSerializer
    permission_classes = [IsAuthenticated]

    filter_backends = (DjangoFilterBackend, OrderingFilter)
    filterset_fields = ["staff", "work_type"]
    ordering_fields = ["created_at"]


# ===========================
# WorkRecord
# ===========================

class WorkRecordViewSet(viewsets.ModelViewSet):
    queryset = (
        WorkRecord.objects.select_related("staff", "work_type")
        .all()
        .order_by("-date", "-start_time")
    )
    serializer_class = WorkRecordSerializer
    permission_classes = [IsAuthenticated]

    filter_backends = (DjangoFilterBackend, OrderingFilter)
    filterset_class = WorkRecordFilter
    ordering_fields = ["date", "created_at", "amount"]

    def perform_create(self, serializer):
        staff = serializer.validated_data["staff"]
        date = serializer.validated_data["date"]

        if is_month_locked(staff, date):
            raise ValidationError("마감된 월의 근무기록은 추가할 수 없습니다.")

        serializer.save()

    def perform_update(self, serializer):
        instance = self.get_object()
        if is_month_locked(instance.staff, instance.date):
            raise ValidationError("마감된 월의 근무기록은 수정할 수 없습니다.")
        serializer.save()

    def perform_destroy(self, instance):
        if is_month_locked(instance.staff, instance.date):
            raise ValidationError("마감된 월의 근무기록은 삭제할 수 없습니다.")
        instance.delete()


# ===========================
# ExpenseRecord
# ===========================

class ExpenseRecordViewSet(viewsets.ModelViewSet):
    queryset = ExpenseRecord.objects.select_related("staff", "approved_by")
    serializer_class = ExpenseRecordSerializer
    permission_classes = [IsAuthenticated]

    filter_backends = (DjangoFilterBackend, OrderingFilter)
    filterset_class = ExpenseRecordFilter
    ordering_fields = ["date", "amount", "created_at"]

    def perform_update(self, serializer):
        instance = self.get_object()

        # ✅ 승인 이후 불변
        if instance.status == "APPROVED":
            raise ValidationError("승인된 비용은 수정할 수 없습니다.")

        new_status = serializer.validated_data.get("status", instance.status)

        if new_status != instance.status:
            user = self.request.user
            is_manager = can_manage_payroll(user)

            if not is_manager:
                raise PermissionDenied("비용 승인/반려는 관리자만 가능합니다.")

            if instance.status != "PENDING":
                raise ValidationError("이미 처리된 비용은 상태를 변경할 수 없습니다.")

            if new_status not in ("APPROVED", "REJECTED"):
                raise ValidationError("유효하지 않은 상태 전이입니다.")

            serializer.save(
                approved_at=timezone.now(),
                approved_by=user,
            )
            return

        serializer.save()


# ===========================
# WorkMonthLock
# ===========================

class WorkMonthLockViewSet(viewsets.ModelViewSet):
    queryset = WorkMonthLock.objects.select_related("staff", "locked_by")
    serializer_class = WorkMonthLockSerializer
    permission_classes = [IsAuthenticated, IsPayrollManager]

    def create(self, request, *args, **kwargs):
        staff = Staff.objects.get(id=request.data.get("staff"))
        year = int(request.data.get("year"))
        month = int(request.data.get("month"))

        obj, _ = WorkMonthLock.objects.update_or_create(
            staff=staff,
            year=year,
            month=month,
            defaults={
                "is_locked": True,
                "locked_by": request.user,
            },
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
# PayrollSnapshot (ReadOnly)
# ===========================

class PayrollSnapshotViewSet(ReadOnlyModelViewSet):
    queryset = PayrollSnapshot.objects.select_related("staff", "generated_by")
    serializer_class = PayrollSnapshotSerializer
    permission_classes = [IsAuthenticated, IsPayrollManager]

    def list(self, request, *args, **kwargs):
        year = request.query_params.get("year")
        month = request.query_params.get("month")
        staff = request.query_params.get("staff")

        qs = self.get_queryset()
        if staff:
            qs = qs.filter(staff_id=staff)
        if year:
            qs = qs.filter(year=year)
        if month:
            qs = qs.filter(month=month)

        return Response(self.get_serializer(qs, many=True).data)

    @action(detail=False, methods=["get"], url_path="export-excel")
    def export_excel(self, request):
        year = request.query_params.get("year")
        month = request.query_params.get("month")

        if not year or not month:
            return Response({"detail": "year, month 필요"}, status=400)

        qs = self.get_queryset().filter(year=year, month=month)

        wb = Workbook()
        ws = wb.active
        ws.title = f"{year}-{month} 급여정산"

        headers = [
            "직원명",
            "연도",
            "월",
            "근무시간",
            "급여",
            "승인된 비용",
            "총 지급액",
            "확정자",
            "확정일시",
        ]
        ws.append(headers)

        for c in ws[1]:
            c.font = Font(bold=True)
            c.alignment = Alignment(horizontal="center")

        tw = te = tt = 0

        for s in qs:
            ws.append([
                s.staff.name,
                s.year,
                s.month,
                float(s.work_hours),
                s.work_amount,
                s.approved_expense_amount,
                s.total_amount,
                getattr(s.generated_by, "username", "") if s.generated_by else "",
                s.created_at.strftime("%Y-%m-%d %H:%M:%S") if s.created_at else "",
            ])
            tw += s.work_amount
            te += s.approved_expense_amount
            tt += s.total_amount

        ws.append(["합계", "", "", "", tw, te, tt, "", ""])

        for col in ws.columns:
            ws.column_dimensions[col[0].column_letter].width = 18

        response = HttpResponse(
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
        response["Content-Disposition"] = (
            f'attachment; filename="payroll_{year}_{month}.xlsx"'
        )
        wb.save(response)
        return response

    @action(detail=False, methods=["get"], url_path="export-pdf")
    def export_pdf(self, request):
        staff_id = request.query_params.get("staff")
        year = request.query_params.get("year")
        month = request.query_params.get("month")

        if not staff_id or not year or not month:
            return Response({"detail": "staff, year, month 필요"}, status=400)

        snap = (
            PayrollSnapshot.objects.filter(
                staff_id=staff_id,
                year=year,
                month=month,
            )
            .select_related("staff", "generated_by")
            .first()
        )

        if not snap:
            return Response({"detail": "급여 스냅샷 없음"}, status=404)

        buffer = BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=A4, leftMargin=40, rightMargin=40, topMargin=40, bottomMargin=40)
        styles = getSampleStyleSheet()
        story = []

        title = f"급여 명세서"
        story.append(Paragraph(title, styles["Title"]))
        story.append(Spacer(1, 12))

        meta_rows = [
            ["직원명", snap.staff.name],
            ["정산월", f"{snap.year}-{snap.month:02d}"],
            ["확정자", getattr(snap.generated_by, "username", "-") if snap.generated_by else "-"],
            ["확정일시", snap.created_at.strftime("%Y-%m-%d %H:%M:%S") if snap.created_at else "-"],
        ]
        meta_table = Table(meta_rows, colWidths=[120, 360])
        meta_table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (0, -1), colors.whitesmoke),
                    ("BOX", (0, 0), (-1, -1), 0.5, colors.grey),
                    ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.lightgrey),
                    ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
                    ("FONTSIZE", (0, 0), (-1, -1), 10),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("PADDING", (0, 0), (-1, -1), 6),
                ]
            )
        )
        story.append(meta_table)
        story.append(Spacer(1, 16))

        rows = [
            ["항목", "값"],
            ["근무시간", f"{snap.work_hours} h"],
            ["급여", f"{snap.work_amount:,} 원"],
            ["승인 비용", f"{snap.approved_expense_amount:,} 원"],
            ["총 지급액", f"{snap.total_amount:,} 원"],
        ]
        t = Table(rows, colWidths=[120, 360])
        t.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f3f4f6")),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.black),
                    ("BOX", (0, 0), (-1, -1), 0.75, colors.grey),
                    ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.lightgrey),
                    ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
                    ("FONTSIZE", (0, 0), (-1, -1), 11),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("PADDING", (0, 0), (-1, -1), 8),
                ]
            )
        )
        story.append(t)
        story.append(Spacer(1, 18))

        story.append(
            Paragraph(
                "※ 본 명세서는 월 마감 시 생성된 불변(스냅샷) 데이터입니다.",
                styles["Normal"],
            )
        )

        doc.build(story)
        pdf = buffer.getvalue()
        buffer.close()

        response = HttpResponse(content_type="application/pdf")
        response["Content-Disposition"] = (
            f'attachment; filename="payroll_{snap.staff.id}_{snap.year}_{snap.month:02d}.pdf"'
        )
        response.write(pdf)
        return response
