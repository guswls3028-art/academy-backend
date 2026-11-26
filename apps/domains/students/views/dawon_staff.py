"""Clock and monthly records for the dedicated synthetic Dawon staff room."""
from collections import defaultdict
from datetime import date

from django.db import transaction
from django.db.models import Count, Q, Sum
from django.utils import timezone
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.models import TenantMembership
from apps.domains.clinic.contracts import is_demo_tenant
from apps.domains.staffs.models import ExpenseRecord, PayrollSnapshot, StaffWorkType, WorkMonthLock, WorkRecord
from apps.domains.staffs.services.work_records import end_work_record, start_work_record
from apps.domains.students import dawon_schema as schema
from apps.domains.students.models import ClassGroupLecture
from apps.domains.students.services.dawon_staff import PREFIX, TEACHER, WORK_TYPE, WORK_MARK, sample_staff


class StaffRequest(APIView):
    permission_classes = [IsAuthenticated]
    role = "admin"

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        tenant, user, token = getattr(request, "tenant", None), request.user, request.auth
        if not (
            is_demo_tenant(tenant) and hasattr(token, "get") and token.get("dawon_demo_role") == self.role
            and user.tenant_id == tenant.pk and user.username == PREFIX + self.role and user.is_active
            and not user.is_staff and not user.is_superuser
            and TenantMembership.objects.filter(tenant=tenant, user=user, role=self.role, is_active=True).exists()
        ):
            raise NotFound()

    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        response["Cache-Control"] = "private, no-store"
        return response


def selected_staff(tenant, pk):
    if not str(pk).isascii() or not str(pk).isdigit() or len(str(pk)) > 16:
        raise NotFound()
    staff = sample_staff(tenant).filter(pk=pk).first()
    if staff is None:
        raise NotFound()
    return staff



def clock_assignment(staff):
    return StaffWorkType.objects.filter(
        tenant=staff.tenant, staff=staff, work_type__tenant=staff.tenant,
        work_type__name=WORK_TYPE, work_type__description=WORK_MARK, work_type__is_active=True,
    )


def month_start(month):
    try:
        first = date.fromisoformat(month + "-01")
        if month != first.strftime("%Y-%m"):
            raise ValueError
    except ValueError:
        raise ValidationError({"month": "연도와 월을 선택해 주세요."})
    return first


def monthly_payroll(tenant, people, first):
    ids = [row["id"] for row in people]
    dated = dict(tenant=tenant, staff_id__in=ids, date__year=first.year, date__month=first.month)
    period = dict(tenant=tenant, staff_id__in=ids, year=first.year, month=first.month)
    records = {row["staff_id"]: row for row in WorkRecord.objects.filter(**dated).values("staff_id").annotate(
        hours=Sum("work_hours"), earned=Sum("amount"), pending=Count("pk", filter=Q(end_time__isnull=True)),
        incomplete=Count("pk", filter=Q(end_time__isnull=False) & (Q(amount__isnull=True) | Q(work_hours__isnull=True))),
        close_blocked=Count("pk", filter=Q(end_time__isnull=False) & (
            Q(resolved_hourly_wage__isnull=True) | Q(current_break_started_at__isnull=False))))}
    expenses = dict(ExpenseRecord.objects.filter(**dated, status="APPROVED").values("staff_id").annotate(
        total=Sum("amount")).values_list("staff_id", "total"))
    pending_expense = set(ExpenseRecord.objects.filter(**dated, status="PENDING").values_list("staff_id", flat=True))
    previous_month = first < timezone.localdate().replace(day=1)
    snapshots = {item.staff_id: item for item in PayrollSnapshot.objects.filter(**period)}
    locked = set(WorkMonthLock.objects.filter(**period, is_locked=True).values_list("staff_id", flat=True))
    for row in people:
        key = row["id"]
        sums = records.get(key, {})
        snapshot = snapshots.get(key)
        if key in locked and snapshot:
            state, hours, work, expense, total = ("confirmed", snapshot.work_hours, snapshot.work_amount,
                                                   snapshot.approved_expense_amount, snapshot.total_amount)
        elif key in locked or snapshot or sums.get("incomplete"):
            state, hours, work, expense, total = "missing", None, None, None, None
        else:
            state, hours, work, expense = "estimate", sums.get("hours") or 0, sums.get("earned") or 0, expenses.get(key, 0)
            total = work + expense
        row.update(state=state, hours=hours, work_amount=work, expense_amount=expense,
                   total_amount=total, open_count=sums.get("pending", 0),
                   can_close=(previous_month and state == "estimate" and row.get("pay_type") == "HOURLY"
                              and not sums.get("pending") and not sums.get("close_blocked") and key not in pending_expense))
    return people


class DawonStaffView(StaffRequest):
    @extend_schema(responses={200: schema.staff_directory}, parameters=[OpenApiParameter("view", str, enum=["payroll"]), OpenApiParameter("staff", int), OpenApiParameter("month", str)])
    def get(self, request):
        tenant = request.tenant
        if request.query_params.get("view") == "payroll":
            month = request.query_params.get("month", "")
            first = month_start(month)
            people = list(sample_staff(tenant).order_by("name", "pk").values("id", "name", "position", "pay_type"))
            for row in monthly_payroll(tenant, people, first):
                row.pop("pay_type")
                hours = row.pop("hours")
                row["minutes"] = round(hours * 60) if hours is not None else None
            return Response({"month": month, "rows": people})
        if "staff" not in request.query_params:
            staff = list(sample_staff(tenant).order_by("name", "pk").values("id", "user_id", "name", "position"))
            classes = defaultdict(list)
            for user_id, name in ClassGroupLecture.objects.filter(
                group__tenant=tenant, lecture__tenant=tenant, reference_teacher_id__in=[row["user_id"] for row in staff],
            ).values_list("reference_teacher_id", "group__name").order_by("group__name").distinct():
                classes[user_id].append(name)
            for row in staff:
                row["classes"] = classes[row.pop("user_id")]
            return Response({"staff": staff})
        staff = selected_staff(tenant, request.query_params["staff"])
        month = request.query_params.get("month", "")
        first = month_start(month)
        row = monthly_payroll(tenant, [{"id": staff.pk}], first)[0]
        payroll = dict(state=row["state"], hours=row["hours"], work=row["work_amount"],
                       expenses=row["expense_amount"], total=row["total_amount"])
        pending = row["open_count"]
        records = WorkRecord.objects.filter(tenant=tenant, staff=staff, date__year=first.year,
                                            date__month=first.month).order_by("-date", "-start_time", "-pk")
        can_clock = clock_assignment(staff).exists() or WorkRecord.objects.filter(
            staff=staff, tenant=tenant, end_time__isnull=True, work_type__tenant=tenant,
            work_type__name=WORK_TYPE, work_type__description=WORK_MARK,
        ).exists()
        return Response({"month": month, "payroll": payroll, "open_count": pending, "can_clock": can_clock, "records": list(
            records.values("id", "date", "start_time", "end_time", "end_date", "work_hours", "amount"))})

    @extend_schema(request={"application/json": schema.record(staff=schema.integer, position=schema.position)},
                   responses={200: schema.record(id=schema.integer, position=schema.position)})
    def patch(self, request):
        if not isinstance(request.data, dict) or set(request.data) != {"staff", "position"} or request.data.get("position") not in ("INSTRUCTOR", "ASSISTANT"):
            raise ValidationError("직원과 표시할 직위를 선택해 주세요.")
        staff = selected_staff(request.tenant, request.data["staff"])
        if staff.position not in ("INSTRUCTOR", "ASSISTANT"):
            raise ValidationError("분원장 직위는 변경할 수 없습니다.")
        staff.position = request.data["position"]
        staff.save(update_fields=["position"])
        return Response({"id": staff.pk, "position": staff.position})


class DawonStaffClockView(StaffRequest):
    role = "teacher"

    def staff(self, request):
        staff = sample_staff(request.tenant).filter(user=request.user, user__username=TEACHER).first()
        if staff is None:
            raise NotFound()
        return staff

    def current(self, staff):
        return WorkRecord.objects.filter(tenant=staff.tenant, staff=staff, end_time__isnull=True).first()

    @extend_schema(responses={200: schema.staff_clock})
    def get(self, request, **kwargs):
        staff = self.staff(request)
        row = self.current(staff)
        return Response({"staff": staff.name, "current": {
            "id": row.pk, "date": row.date, "start_time": row.start_time
        } if row else None})

    @extend_schema(request={"application/json": schema.clock_request}, responses={200: schema.staff_clock})
    @transaction.atomic
    def post(self, request, **kwargs):
        if not isinstance(request.data, dict):
            raise ValidationError("지원하지 않는 근태 요청입니다.")
        staff = self.staff(request)
        now = timezone.localtime()
        if request.data == {"action": "start"}:
            assignment = clock_assignment(staff).first()
            if not assignment:
                raise ValidationError("배정된 체험 근무 유형이 없습니다.")
            start_work_record(staff=staff, work_type_id=assignment.work_type_id, date=now.date(),
                              start_time=now.time(), require_assignment=True)
        elif set(request.data) == {"action", "record"} and request.data.get("action") == "end":
            row = self.current(staff)
            if not row or row.pk != request.data["record"]:
                raise ValidationError("현재 출근 기록이 변경되었습니다. 다시 확인해 주세요.")
            end_work_record(record=row, ended_at=now)
        else:
            raise ValidationError("지원하지 않는 근태 요청입니다.")
        return self.get(request)


@extend_schema(exclude=True)
class DawonAdminStaffClockView(DawonStaffClockView):
    role = "admin"

    def staff(self, request):
        return selected_staff(request.tenant, self.kwargs["staff_id"])

    def current(self, staff):
        return WorkRecord.objects.filter(
            tenant=staff.tenant, staff=staff, end_time__isnull=True, work_type__tenant=staff.tenant,
            work_type__name=WORK_TYPE, work_type__description=WORK_MARK,
        ).first()
