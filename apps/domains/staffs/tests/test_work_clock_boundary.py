from datetime import date, datetime, time

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.core.models import Tenant, TenantMembership
from apps.domains.staffs.models import Staff, StaffWorkType, WorkRecord, WorkType
from apps.domains.staffs.selectors import (
    current_work_record_for_staff,
    work_current_status,
    work_records_for_staff_range,
)
from apps.domains.staffs.services import (
    end_work_break,
    end_work_record,
    start_work_break,
    start_work_record,
)


User = get_user_model()


class WorkClockBoundaryTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(
            code="work-clock-boundary",
            name="Work Clock Boundary",
            is_active=True,
        )
        self.user = User.objects.create_user(
            username="work-clock-staff",
            password="test1234",
            tenant=self.tenant,
            is_staff=True,
        )
        TenantMembership.ensure_active(
            tenant=self.tenant,
            user=self.user,
            role="staff",
        )
        self.staff = Staff.objects.create(
            tenant=self.tenant,
            user=self.user,
            name="경계 조교",
            phone="01011112222",
        )
        self.work_type = WorkType.objects.create(
            tenant=self.tenant,
            name="클리닉 조교",
            base_hourly_wage=10_000,
            is_active=True,
        )
        StaffWorkType.objects.create(
            tenant=self.tenant,
            staff=self.staff,
            work_type=self.work_type,
        )

    @staticmethod
    def _at(hour: int, minute: int):
        return timezone.make_aware(datetime(2026, 8, 20, hour, minute))

    def _start(self):
        return start_work_record(
            staff=self.staff,
            work_type_id=self.work_type.id,
            date=date(2026, 8, 20),
            start_time=time(9, 0),
            require_assignment=True,
        )

    def test_service_owns_break_resume_and_payroll_close(self):
        record = self._start()

        start_work_break(record=record, started_at=self._at(9, 30))
        end_work_break(record=record, ended_at=self._at(9, 45))
        closed = end_work_record(record=record, ended_at=self._at(10, 0))

        self.assertEqual(closed.break_total_seconds, 15 * 60)
        self.assertEqual(closed.break_minutes, 15)
        self.assertEqual(closed.work_hours, 0.75)
        self.assertEqual(closed.resolved_hourly_wage, 10_000)
        self.assertEqual(closed.amount, 7_500)

    def test_clock_close_preserves_elapsed_days_and_payroll_after_reload(self):
        for days, hours in ((1, 24), (2, 48)):
            with self.subTest(days=days):
                record = self._start()
                closed = end_work_record(
                    record=record,
                    ended_at=timezone.make_aware(datetime(2026, 8, 20 + days, 9)),
                )
                closed.refresh_from_db()
                self.assertEqual(closed.work_hours, hours)
                self.assertEqual(closed.amount, hours * 10_000)
                self.assertEqual(closed.calculate_payroll()[:2], (hours, hours * 10_000))

    def test_clock_close_uses_actual_overnight_date_when_end_time_is_later(self):
        record = self._start()
        closed = end_work_record(
            record=record,
            ended_at=timezone.make_aware(datetime(2026, 8, 21, 10)),
            meal_minutes=60,
        )
        closed.refresh_from_db()
        self.assertEqual(closed.work_hours, 24)
        self.assertEqual(closed.amount, 240_000)

    def test_clock_rejects_end_before_start_without_fabricating_overnight_work(self):
        record = self._start()
        with self.assertRaises(ValidationError):
            end_work_record(record=record, ended_at=self._at(8, 0))
        record.refresh_from_db()
        self.assertIsNone(record.end_time)
        self.assertIsNone(record.amount)

    def test_clock_normalizes_utc_end_to_local_payroll_date(self):
        from datetime import timezone as datetime_timezone

        record = self._start()
        closed = end_work_record(
            record=record,
            ended_at=datetime(2026, 8, 21, 1, tzinfo=datetime_timezone.utc),
        )
        self.assertEqual(closed.end_date, date(2026, 8, 21))
        self.assertEqual(closed.end_time, time(10))
        self.assertEqual(closed.amount, 250_000)

    def test_excessive_clock_duration_has_recoverable_error_and_remains_open(self):
        record = self._start()
        with self.assertRaises(ValidationError):
            end_work_record(
                record=record,
                ended_at=timezone.make_aware(datetime(2026, 10, 1, 10)),
            )
        record.refresh_from_db()
        self.assertIsNone(record.end_time)
        self.assertIsNone(record.end_date)

    def test_explicit_zero_assignment_wage_does_not_fall_back_to_base_wage(self):
        assignment = StaffWorkType.objects.get(
            tenant=self.tenant,
            staff=self.staff,
            work_type=self.work_type,
        )
        assignment.hourly_wage = 0
        assignment.save(update_fields=["hourly_wage"])

        record = self._start()
        closed = end_work_record(record=record, ended_at=self._at(10, 0))

        self.assertEqual(closed.resolved_hourly_wage, 0)
        self.assertEqual(closed.amount, 0)

    def test_invalid_close_rolls_back_active_break_and_open_record(self):
        record = self._start()
        break_started_at = self._at(9, 10)
        start_work_break(record=record, started_at=break_started_at)

        with self.assertRaises(ValidationError):
            end_work_record(
                record=record,
                ended_at=self._at(9, 20),
                meal_minutes=10,
            )

        record.refresh_from_db()
        self.assertIsNone(record.end_time)
        self.assertEqual(record.current_break_started_at, break_started_at)
        self.assertEqual(record.break_total_seconds, 0)

    def test_selectors_fail_closed_and_return_runtime_contract(self):
        record = self._start()
        selected = current_work_record_for_staff(
            tenant=self.tenant,
            staff=self.staff,
        )
        self.assertEqual(selected.id, record.id)
        self.assertEqual(
            work_current_status(selected),
            {
                "status": "WORKING",
                "work_record_id": record.id,
                "date": "2026-08-20",
                "started_at": "09:00:00",
                "work_type": self.work_type.id,
                "work_type_name": "클리닉 조교",
                "hourly_wage": 10_000,
                "break_minutes": 0,
                "break_total_seconds": 0,
            },
        )

        other_tenant = Tenant.objects.create(
            code="work-clock-other",
            name="Other Tenant",
            is_active=True,
        )
        with self.assertRaises(PermissionDenied):
            work_records_for_staff_range(
                tenant=other_tenant,
                staff=self.staff,
                date_from=date(2026, 8, 1),
                date_to=date(2026, 8, 31),
            )
