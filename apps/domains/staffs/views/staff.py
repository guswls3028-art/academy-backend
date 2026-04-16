# PATH: apps/domains/staffs/views/staff.py

from django.db import transaction
from django.utils import timezone

from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.filters import SearchFilter, OrderingFilter
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework.exceptions import ValidationError

from ..models import Staff, WorkRecord, ExpenseRecord
from ..serializers import (
    StaffListSerializer,
    StaffDetailSerializer,
    StaffCreateUpdateSerializer,
    WorkRecordSerializer,
)
from academy.adapters.db.django import repositories_staffs as staff_repo
from academy.adapters.db.django import repositories_core as core_repo
from ..filters import StaffFilter
from apps.core.models import TenantMembership
from apps.core.permissions import is_effective_staff, TenantResolvedAndMember, TenantResolvedAndStaff
from .helpers import (
    _owner_display_for_tenant,
    IsPayrollManager,
    is_month_locked,
    can_manage_payroll,
)

# ===========================
# Staff
# ===========================

class StaffViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated, IsPayrollManager]

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

    def get_queryset(self):
        qs = staff_repo.staff_queryset_tenant(self.request.tenant)
        # list 액션: 오너 Staff 제외 (owner 영역에서 별도 표시하므로 중복 방지)
        if self.action == "list":
            tenant = getattr(self.request, "tenant", None)
            if tenant:
                owner_user_id = TenantMembership.objects.filter(
                    tenant=tenant, role="owner", is_active=True
                ).values_list("user_id", flat=True).first()
                if owner_user_id:
                    qs = qs.exclude(user_id=owner_user_id)
        return qs

    def list(self, request, *args, **kwargs):
        response = super().list(request, *args, **kwargs)
        tenant = getattr(request, "tenant", None)
        owner = _owner_display_for_tenant(tenant, request)
        # Pagination 없으면 response.data 가 list 이므로 dict 로 감싼 뒤 owner 추가
        if isinstance(response.data, list):
            response.data = {"results": response.data, "owner": owner}
        else:
            response.data["owner"] = owner
        return response

    def perform_create(self, serializer):
        serializer.save(tenant=self.request.tenant)

    def perform_destroy(self, instance):
        serializer = self.get_serializer(instance)
        serializer.delete(instance)

    @action(detail=True, methods=["post"], url_path="change-password")
    def change_password(self, request, pk=None):
        """직원 비밀번호 변경. Body: { "password": "..." }"""
        staff = self.get_object()
        if not staff.user:
            raise ValidationError("이 직원에게 연결된 계정이 없습니다.")

        new_password = (request.data.get("password") or "").strip()
        if not new_password:
            raise ValidationError({"password": "새 비밀번호를 입력하세요."})
        if len(new_password) < 4:
            raise ValidationError({"password": "비밀번호는 4자 이상이어야 합니다."})

        from apps.core.services.password import force_reset_password
        force_reset_password(staff.user, new_password)
        return Response({"detail": "비밀번호가 변경되었습니다."})

    @action(detail=False, methods=["get"], url_path="me", permission_classes=[IsAuthenticated, TenantResolvedAndMember])
    def me(self, request):
        import logging
        logger = logging.getLogger(__name__)
        tenant = getattr(request, "tenant", None)
        try:
            from academy.adapters.db.django import repositories_core as core_repo
            is_owner = bool(
                tenant
                and request.user.is_authenticated
                and core_repo.membership_exists_staff(tenant=tenant, user=request.user, staff_roles=("owner",))
            )
            is_de_facto_owner = is_owner or is_effective_staff(request.user, tenant)
            owner_display_name = None
            owner_phone = None
            if is_de_facto_owner and request.user:
                owner_display_name = (getattr(request.user, "name", None) or "").strip() or getattr(request.user, "username", "") or "원장"
                owner_phone = (getattr(request.user, "phone", None) or "").strip() or None

            payload = {
                "is_authenticated": True,
                "is_superuser": bool(request.user.is_superuser),
                "is_staff": bool(request.user.is_staff),
                "is_payroll_manager": can_manage_payroll(request.user, tenant),
                "is_owner": is_owner,
                "owner_display_name": owner_display_name,
                "owner_phone": owner_phone,
            }

            # 직원(Staff)으로 로그인한 경우: 출근/퇴근용 staff_id, default_work_type_id
            # 오너여도 동일하게 출퇴근 기록 가능하도록: 오너인데 Staff가 없으면 해당 테넌트에 Staff 생성 후 연결
            # staff_profile = OneToOneField reverse → 다른 테넌트 Staff일 수 있으므로 tenant_id 확인 필수
            staff_profile = getattr(request.user, "staff_profile", None)
            staff_in_tenant = (
                staff_profile
                if staff_profile and tenant and getattr(staff_profile, "tenant_id", None) == tenant.id
                else None
            )
            if staff_in_tenant:
                payload["staff_id"] = staff_in_tenant.id
                first_swt = staff_in_tenant.staff_work_types.order_by("id").first()
                if first_swt:
                    payload["default_work_type_id"] = first_swt.work_type_id
            elif is_de_facto_owner and tenant and request.user:
                # staff_profile이 다른 테넌트에 있거나 없을 때: 현재 테넌트에서 Staff 조회/생성
                from apps.domains.staffs.models import Staff, StaffWorkType, WorkType
                owner_staff = Staff.objects.filter(tenant=tenant, user=request.user).first()
                if not owner_staff:
                    # user OneToOne이 이미 다른 테넌트 Staff에 연결된 경우 user=None으로 생성
                    can_link_user = staff_profile is None
                    owner_name = (getattr(request.user, "name", None) or "").strip() or getattr(request.user, "username", "") or "원장"
                    owner_phone = (getattr(request.user, "phone", None) or "").strip() or ""
                    with transaction.atomic():
                        owner_staff, _created = Staff.objects.get_or_create(
                            tenant=tenant,
                            name=owner_name,
                            phone=owner_phone or "",
                            defaults={
                                "user": request.user if can_link_user else None,
                                "is_manager": True,
                            },
                        )
                first_wt = WorkType.objects.filter(tenant=tenant, is_active=True).order_by("id").first()
                if not first_wt:
                    first_wt = WorkType.objects.create(
                        tenant=tenant,
                        name="기본",
                        base_hourly_wage=0,
                        is_active=True,
                    )
                if not owner_staff.staff_work_types.exists():
                    StaffWorkType.objects.get_or_create(
                        tenant=tenant,
                        staff=owner_staff,
                        work_type=first_wt,
                        defaults={"hourly_wage": None},
                    )
                payload["staff_id"] = owner_staff.id
                payload["default_work_type_id"] = first_wt.id

            return Response(payload)
        except Exception as e:
            logger.warning("staffs/me error: %s", e, exc_info=True)
            return Response(
                {
                    "is_authenticated": True,
                    "is_superuser": bool(getattr(request.user, "is_superuser", False)),
                    "is_staff": bool(getattr(request.user, "is_staff", False)),
                    "is_payroll_manager": False,
                    "is_owner": False,
                    "owner_display_name": None,
                    "owner_phone": None,
                }
            )

    def _staff_display_role(self, tenant, staff) -> str:
        """직원관리 목록·헤더 근무자 아바타와 동일한 직급 판별: owner(대표) / TEACHER(강사) / ASSISTANT(조교)."""
        if getattr(staff, "user_id", None) and getattr(staff, "user", None):
            if core_repo.membership_exists_staff(tenant, staff.user, staff_roles=("owner",)):
                return "owner"
        from academy.adapters.db.django import repositories_teachers as teacher_repo
        if teacher_repo.teacher_exists_tenant_name_phone(tenant, staff.name, staff.phone or ""):
            return "TEACHER"
        return "ASSISTANT"

    @action(detail=False, methods=["get"], url_path="currently-working", permission_classes=[IsAuthenticated, TenantResolvedAndStaff])
    def currently_working(self, request):
        """현재 근무 중인 직원 목록 (end_time 이 null 인 WorkRecord 가 있는 직원). 직급(role) + 근무 시작 시각·휴식 정보(드롭다운용)."""
        tenant = getattr(request, "tenant", None)
        if not tenant:
            return Response([])
        records = (
            WorkRecord.objects
            .filter(tenant=tenant, end_time__isnull=True)
            .select_related("staff")
            .order_by("staff_id", "-date", "-start_time")
        )
        seen_staff = set()
        record_by_staff = {}
        for rec in records:
            if rec.staff_id not in seen_staff:
                seen_staff.add(rec.staff_id)
                record_by_staff[rec.staff_id] = rec
        staff_ids = list(record_by_staff.keys())
        staffs = Staff.objects.filter(id__in=staff_ids).select_related("user").only("id", "name", "phone", "tenant_id", "user_id")
        out = []
        for s in staffs:
            try:
                role = self._staff_display_role(tenant, s)
            except Exception:
                role = "ASSISTANT"
            rec = record_by_staff.get(s.id)
            item = {"staff_id": s.id, "staff_name": s.name, "role": role}
            if rec:
                item["date"] = rec.date.isoformat()
                item["started_at"] = rec.start_time.strftime("%H:%M:%S") if hasattr(rec.start_time, "strftime") else str(rec.start_time)
                item["break_minutes"] = getattr(rec, "break_minutes", 0) or 0
                item["break_total_seconds"] = getattr(rec, "break_total_seconds", 0) or (item["break_minutes"] * 60)
                if getattr(rec, "current_break_started_at", None):
                    item["break_started_at"] = rec.current_break_started_at.isoformat()
            out.append(item)
        return Response(out)

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

        # JSON 직렬화를 위해 time/datetime을 문자열로 변환
        started_at_str = (
            record.start_time.strftime("%H:%M:%S")
            if hasattr(record.start_time, "strftime")
            else str(record.start_time)
        )

        if record.current_break_started_at:
            break_sec = getattr(record, "break_total_seconds", 0) or (record.break_minutes * 60)
            return Response({
                "status": "BREAK",
                "work_record_id": record.id,
                "date": record.date.isoformat(),
                "started_at": started_at_str,
                "break_minutes": record.break_minutes,
                "break_total_seconds": break_sec,
                "break_started_at": record.current_break_started_at.isoformat(),
            })

        break_sec = getattr(record, "break_total_seconds", 0) or (record.break_minutes * 60)
        return Response({
            "status": "WORKING",
            "work_record_id": record.id,
            "date": record.date.isoformat(),
            "started_at": started_at_str,
            "break_minutes": record.break_minutes,
            "break_total_seconds": break_sec,
        })

    @action(detail=True, methods=["post"], url_path="work-records/start-work")
    def start_work(self, request, pk=None):
        staff = self.get_object()
        now = timezone.localtime(timezone.now())

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

    @action(detail=True, methods=["get"], url_path="summary")
    def summary(self, request, pk=None):
        """직원별 기간 집계. 쿼리: date_from, date_to (YYYY-MM-DD)."""
        staff = self.get_object()
        date_from = request.query_params.get("date_from")
        date_to = request.query_params.get("date_to")
        if not date_from or not date_to:
            raise ValidationError("date_from, date_to는 필수입니다.")
        from datetime import datetime
        try:
            df = datetime.strptime(date_from, "%Y-%m-%d").date()
            dt = datetime.strptime(date_to, "%Y-%m-%d").date()
        except ValueError:
            raise ValidationError("date_from, date_to는 YYYY-MM-DD 형식이어야 합니다.")
        if df > dt:
            raise ValidationError("date_from은 date_to 이전이어야 합니다.")

        from django.db.models import Sum
        wr_qs = WorkRecord.objects.filter(
            staff=staff, tenant=staff.tenant, date__gte=df, date__lte=dt
        )
        er_qs = ExpenseRecord.objects.filter(
            staff=staff, tenant=staff.tenant,
            date__gte=df, date__lte=dt, status="APPROVED",
        )

        work_agg = wr_qs.aggregate(total_hours=Sum("work_hours"), total_amount=Sum("amount"))
        expense_agg = er_qs.aggregate(total=Sum("amount"))

        work_hours = float(work_agg["total_hours"] or 0)
        work_amount = int(work_agg["total_amount"] or 0)
        expense_amount = int(expense_agg["total"] or 0)
        total_amount = work_amount + expense_amount

        return Response({
            "staff_id": staff.id,
            "work_hours": work_hours,
            "work_amount": work_amount,
            "expense_amount": expense_amount,
            "total_amount": total_amount,
        })
