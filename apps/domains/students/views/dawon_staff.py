"""Clock and monthly records for the dedicated synthetic Dawon staff room."""
from collections import defaultdict
from datetime import date

from django.db import transaction
from django.db.models import Q, Sum
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


class DawonStaffView(StaffRequest):
    @extend_schema(responses={200: schema.staff_directory}, parameters=[OpenApiParameter("staff", int), OpenApiParameter("month", str)])
    def get(self, request):
        tenant = request.tenant
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
        try:
            first = date.fromisoformat(month + "-01")
            if month != first.strftime("%Y-%m"):
                raise ValueError
        except ValueError:
            raise ValidationError({"month": "연도와 월을 선택해 주세요."})
        period = dict(tenant=tenant, staff=staff, year=first.year, month=first.month)
        dated = dict(tenant=tenant, staff=staff, date__year=first.year, date__month=first.month)
        records = WorkRecord.objects.filter(**dated).order_by("-date", "-start_time", "-pk")
        amounts = records.aggregate(hours=Sum("work_hours"), earned=Sum("amount"))
        expenses = ExpenseRecord.objects.filter(**dated, status="APPROVED").aggregate(total=Sum("amount"))["total"] or 0
        snapshot = PayrollSnapshot.objects.filter(**period).first()
        locked = WorkMonthLock.objects.filter(**period, is_locked=True).exists()
        pending = records.filter(end_time__isnull=True).count()
        incomplete = records.filter(end_time__isnull=False).filter(Q(amount__isnull=True) | Q(work_hours__isnull=True)).exists()
        if snapshot and locked:
            payroll = {"state": "confirmed", "hours": snapshot.work_hours, "work": snapshot.work_amount,
                       "expenses": snapshot.approved_expense_amount, "total": snapshot.total_amount}
        elif locked or snapshot or incomplete:
            payroll = {"state": "missing", "hours": None, "work": None, "expenses": None, "total": None}
        else:
            earned = amounts["earned"] or 0
            payroll = {"state": "estimate", "hours": amounts["hours"] or 0, "work": earned,
                       "expenses": expenses, "total": earned + expenses}
        return Response({"month": month, "payroll": payroll, "open_count": pending, "records": list(
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
    def get(self, request):
        staff = self.staff(request)
        row = self.current(staff)
        return Response({"staff": staff.name, "current": {
            "id": row.pk, "date": row.date, "start_time": row.start_time
        } if row else None})

    @extend_schema(request={"application/json": schema.clock_request}, responses={200: schema.staff_clock})
    @transaction.atomic
    def post(self, request):
        if not isinstance(request.data, dict):
            raise ValidationError("지원하지 않는 근태 요청입니다.")
        staff = self.staff(request)
        now = timezone.localtime()
        if request.data == {"action": "start"}:
            assignment = StaffWorkType.objects.filter(
                tenant=staff.tenant, staff=staff, work_type__tenant=staff.tenant,
                work_type__name=WORK_TYPE, work_type__description=WORK_MARK, work_type__is_active=True,
            ).first()
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
