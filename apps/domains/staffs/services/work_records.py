from __future__ import annotations

from django.db import IntegrityError, transaction
from rest_framework.exceptions import APIException
from rest_framework.exceptions import ValidationError

from academy.adapters.db.django import repositories_staffs as staff_repo


class OpenWorkRecordConflict(APIException):
    status_code = 409
    default_detail = "이미 근무 중입니다."
    default_code = "open_work_record_exists"


def has_open_work_record_conflict(*, staff, exclude_record_id: int | None = None) -> bool:
    """Re-check the invariant after a failed transaction has rolled back."""
    return staff_repo.work_record_open_exists(
        staff,
        exclude_record_id=exclude_record_id,
    )


def start_work_record(*, staff, work_type_id: int, date, start_time):
    """Create one open clock-in record or fail with a deterministic conflict."""
    try:
        with transaction.atomic():
            # Phase-A rolling-deploy invariant: every sanctioned writer locks
            # the canonical Staff row before checking/creating an open record.
            # This remains correct before the phase-B partial unique constraint
            # is installed.
            locked_staff = staff_repo.staff_get_for_update(
                staff.tenant_id,
                staff.pk,
            )
            if staff_repo.is_month_locked(
                locked_staff,
                date.year,
                date.month,
            ):
                raise ValidationError(
                    "마감된 월입니다. 근무기록을 추가할 수 없습니다."
                )
            if staff_repo.work_record_filter_open(locked_staff).exists():
                raise OpenWorkRecordConflict()
            return staff_repo.work_record_create_start(
                staff=locked_staff,
                work_type_id=work_type_id,
                date=date,
                start_time=start_time,
            )
    except IntegrityError as exc:
        # Preserve the original error unless the database now contains a
        # conflicting open record (including after the phase-B constraint).
        if has_open_work_record_conflict(staff=staff):
            raise OpenWorkRecordConflict() from exc
        raise
