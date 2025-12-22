# apps/staffs/views.py

from django.db.models import Sum
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.filters import SearchFilter, OrderingFilter
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated

from .models import (
    Staff,
    WorkType,
    StaffWorkType,
    WorkRecord,
    ExpenseRecord,
)
from .serializers import (
    WorkTypeSerializer,
    StaffWorkTypeSerializer,
    StaffListSerializer,
    StaffDetailSerializer,
    StaffCreateUpdateSerializer,
    WorkRecordSerializer,
    ExpenseRecordSerializer,
)
from .filters import StaffFilter, WorkRecordFilter, ExpenseRecordFilter


# ---------------------------
# WorkType (근무 유형 정의)
# ---------------------------

class WorkTypeViewSet(viewsets.ModelViewSet):
    queryset = WorkType.objects.all().order_by("name")
    serializer_class = WorkTypeSerializer
    permission_classes = [IsAuthenticated]

    filter_backends = (DjangoFilterBackend, SearchFilter, OrderingFilter)
    filterset_fields = ["is_active"]
    search_fields = ["name", "description"]
    ordering_fields = ["name", "base_hourly_wage", "created_at"]


# ---------------------------
# Staff (조교)
# ---------------------------

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

    # /api/staffs/{id}/work-types/
    @action(detail=True, methods=["get", "post"], url_path="work-types")
    def work_types(self, request, pk=None):
        staff = self.get_object()

        if request.method.lower() == "get":
            qs = staff.staff_work_types.select_related("work_type").all()
            serializer = StaffWorkTypeSerializer(qs, many=True)
            return Response(serializer.data)

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

    # /api/staffs/{id}/summary/?date_from=&date_to=
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

        work_amount = wr_qs.aggregate(total=Sum("amount"))["total"] or 0
        work_hours = wr_qs.aggregate(total=Sum("work_hours"))["total"] or 0
        expense_amount = er_qs.aggregate(total=Sum("amount"))["total"] or 0

        return Response(
            {
                "staff_id": staff.id,
                "work_hours": work_hours,
                "work_amount": work_amount,
                "expense_amount": expense_amount,
                "total_amount": work_amount + expense_amount,
            }
        )


# ---------------------------
# StaffWorkType (조교-근무유형 매핑)
# ---------------------------

class StaffWorkTypeViewSet(viewsets.ModelViewSet):
    """
    /api/staff-work-types/?staff=1
    PATCH / DELETE 지원
    """

    queryset = StaffWorkType.objects.select_related("staff", "work_type").all()
    serializer_class = StaffWorkTypeSerializer
    permission_classes = [IsAuthenticated]

    filter_backends = (DjangoFilterBackend, OrderingFilter)
    filterset_fields = ["staff", "work_type"]
    ordering_fields = ["created_at"]


# ---------------------------
# WorkRecord (출퇴근 기록)
# ---------------------------

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

    # /api/work-records/my/
    @action(detail=False, methods=["get"], url_path="my")
    def my_records(self, request):
        staff = getattr(request.user, "staff_profile", None)
        if not staff:
            return Response(
                {"detail": "Staff profile not found."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        qs = self.get_queryset().filter(staff=staff)

        date_from = request.query_params.get("date_from")
        date_to = request.query_params.get("date_to")
        if date_from:
            qs = qs.filter(date__gte=date_from)
        if date_to:
            qs = qs.filter(date__lte=date_to)

        serializer = self.get_serializer(qs, many=True)
        return Response(serializer.data)


# ---------------------------
# ExpenseRecord (비용 기록)
# ---------------------------

class ExpenseRecordViewSet(viewsets.ModelViewSet):
    queryset = (
        ExpenseRecord.objects.select_related("staff")
        .all()
        .order_by("-date", "-id")
    )
    serializer_class = ExpenseRecordSerializer
    permission_classes = [IsAuthenticated]

    filter_backends = (DjangoFilterBackend, OrderingFilter)
    filterset_class = ExpenseRecordFilter
    ordering_fields = ["date", "amount", "created_at"]

    # /api/expense-records/my/
    @action(detail=False, methods=["get"], url_path="my")
    def my_expenses(self, request):
        staff = getattr(request.user, "staff_profile", None)
        if not staff:
            return Response(
                {"detail": "Staff profile not found."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        qs = self.get_queryset().filter(staff=staff)

        date_from = request.query_params.get("date_from")
        date_to = request.query_params.get("date_to")
        if date_from:
            qs = qs.filter(date__gte=date_from)
        if date_to:
            qs = qs.filter(date__lte=date_to)

        serializer = self.get_serializer(qs, many=True)
        return Response(serializer.data)
