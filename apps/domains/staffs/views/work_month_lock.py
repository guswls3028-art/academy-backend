# PATH: apps/domains/staffs/views/work_month_lock.py

from django.db import transaction
from django_filters.rest_framework import DjangoFilterBackend

from rest_framework import mixins, status, viewsets
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.exceptions import ValidationError

from ..models import Staff
from ..serializers import WorkMonthLockSerializer
from academy.adapters.db.django import repositories_staffs as staff_repo
from .helpers import IsPayrollManager, generate_payroll_snapshot


class WorkMonthLockReconciliationRequired(Exception):
    def __init__(self, reason):
        self.reason = reason
        super().__init__(reason)

# ===========================
# WorkMonthLock
# ===========================

class WorkMonthLockViewSet(
    mixins.CreateModelMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    """Append-only payroll close boundary; reopen requires a separate workflow."""
    serializer_class = WorkMonthLockSerializer
    permission_classes = [IsAuthenticated, IsPayrollManager]
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ["staff", "year", "month", "is_locked"]

    def get_queryset(self):
        return staff_repo.work_month_lock_queryset_tenant(self.request.tenant)

    def create(self, request, *args, **kwargs):
        staff_id_raw = request.data.get("staff")
        year_raw = request.data.get("year")
        month_raw = request.data.get("month")
        if staff_id_raw is None:
            raise ValidationError("staff는 필수입니다.")
        if year_raw is None or month_raw is None:
            raise ValidationError("year, month는 필수입니다.")

        def parse_integer(value, *, field):
            if isinstance(value, bool) or not isinstance(value, (int, str)):
                raise ValidationError(f"{field}는 정수여야 합니다.")
            if isinstance(value, str) and not value.strip().isdigit():
                raise ValidationError(f"{field}는 정수여야 합니다.")
            parsed = int(value)
            if parsed < 1:
                raise ValidationError(f"{field}는 양의 정수여야 합니다.")
            return parsed

        try:
            staff_id = parse_integer(staff_id_raw, field="staff")
            year = parse_integer(year_raw, field="year")
            month = parse_integer(month_raw, field="month")
        except (TypeError, ValueError) as exc:
            raise ValidationError("staff, year, month는 정수여야 합니다.") from exc
        if not (2020 <= year <= 2100):
            raise ValidationError("year는 2020~2100 사이여야 합니다.")
        if not (1 <= month <= 12):
            raise ValidationError("month는 1~12 사이여야 합니다.")

        try:
            with transaction.atomic():
                # Same Staff mutex as WorkRecord/ExpenseRecord writers.  Once
                # acquired, no writer can commit into this month between the
                # lock row and immutable snapshot creation.
                staff = staff_repo.staff_get_for_update(
                    request.tenant.id,
                    staff_id,
                )
                blockers = staff_repo.payroll_close_blockers(
                    staff,
                    year,
                    month,
                )
                if any(
                    blockers[key]
                    for key in (
                        "open_work_record_ids",
                        "incomplete_work_record_ids",
                        "pending_expense_ids",
                    )
                ):
                    raise ValidationError(
                        {
                            "detail": "미완료 근무 또는 미처리 비용이 있어 월마감할 수 없습니다.",
                            **blockers,
                        }
                    )

                snapshot_exists = staff_repo.payroll_snapshot_exists_staff(
                    staff,
                    year,
                    month,
                )
                obj, created = staff_repo.work_month_lock_get_or_create_defaults(
                    request.tenant,
                    staff,
                    year,
                    month,
                    defaults={"is_locked": True, "locked_by": request.user},
                )
                if created and snapshot_exists:
                    raise WorkMonthLockReconciliationRequired(
                        "snapshot_exists_without_lock"
                    )
                if not created and (not obj.is_locked or not snapshot_exists):
                    raise WorkMonthLockReconciliationRequired(
                        "legacy_lock_or_snapshot_mismatch"
                    )
                if created:
                    generate_payroll_snapshot(
                        staff=staff,
                        year=year,
                        month=month,
                        user=request.user,
                    )
        except Staff.DoesNotExist:
            raise ValidationError("해당 직원을 찾을 수 없습니다.")
        except WorkMonthLockReconciliationRequired as exc:
            from apps.core.services.ops_audit import record_audit

            record_audit(
                request,
                action="payroll.month_lock_reconciliation_required",
                target_tenant=request.tenant,
                summary=(
                    f"staff={staff_id} year={year} month={month} "
                    f"reason={exc.reason}"
                ),
                payload={
                    "staff_id": staff_id,
                    "year": year,
                    "month": month,
                    "reason": exc.reason,
                },
                result="failed",
                error=exc.reason,
            )
            raise ValidationError(
                "기존 월마감/스냅샷 상태가 일치하지 않습니다. 운영자 대사가 필요합니다."
            ) from exc

        return Response(
            WorkMonthLockSerializer(obj).data,
            status=(
                status.HTTP_201_CREATED
                if created
                else status.HTTP_200_OK
            ),
        )
