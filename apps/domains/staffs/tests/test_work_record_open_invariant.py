from datetime import date, time
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.db import IntegrityError
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.models import Tenant, TenantMembership
from apps.domains.staffs.models import Staff, WorkMonthLock, WorkRecord, WorkType
from apps.domains.staffs.services import OpenWorkRecordConflict, start_work_record
from apps.domains.staffs.views import StaffViewSet, WorkRecordViewSet


User = get_user_model()


class WorkRecordOpenInvariantTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.tenant = Tenant.objects.create(code="work-open", name="Work Open", is_active=True)
        self.owner = User.objects.create_user(
            username="work-open-owner",
            password="test1234",
            tenant=self.tenant,
            is_staff=True,
        )
        TenantMembership.ensure_active(tenant=self.tenant, user=self.owner, role="owner")
        self.staff = Staff.objects.create(
            tenant=self.tenant,
            user=self.owner,
            name="근무자",
            phone="01011112222",
        )
        self.work_type = WorkType.objects.create(
            tenant=self.tenant,
            name="기본 근무",
            base_hourly_wage=10000,
            is_active=True,
        )

    def _create_open_record(self):
        return WorkRecord.objects.create(
            tenant=self.tenant,
            staff=self.staff,
            work_type=self.work_type,
            date=date(2026, 7, 13),
            start_time=time(9, 0),
        )

    def test_service_rejects_second_open_record_for_same_staff(self):
        self._create_open_record()

        with self.assertRaises(OpenWorkRecordConflict):
            start_work_record(
                staff=self.staff,
                work_type_id=self.work_type.id,
                date=date(2026, 7, 13),
                start_time=time(10, 0),
            )

        self.assertEqual(
            WorkRecord.objects.filter(staff=self.staff, end_time__isnull=True).count(),
            1,
        )

    def test_clock_in_api_returns_409_when_open_record_exists(self):
        self._create_open_record()
        request = self.factory.post(
            f"/api/v1/staffs/{self.staff.id}/work-records/start-work/",
            {"work_type": self.work_type.id},
            format="json",
        )
        request.tenant = self.tenant
        force_authenticate(request, user=self.owner)

        response = StaffViewSet.as_view({"post": "start_work"})(request, pk=self.staff.id)

        self.assertEqual(response.status_code, 409, response.data)
        self.assertEqual(str(response.data["detail"]), "이미 근무 중입니다.")

    def test_clock_in_unrelated_integrity_error_is_not_masked_as_open_conflict(self):
        with patch(
            "apps.domains.staffs.services.work_records.staff_repo.work_record_create_start",
            side_effect=IntegrityError("unrelated database constraint"),
        ):
            with self.assertRaisesMessage(IntegrityError, "unrelated database constraint"):
                start_work_record(
                    staff=self.staff,
                    work_type_id=self.work_type.id,
                    date=date(2026, 7, 13),
                    start_time=time(10, 0),
                )

    def test_direct_create_unrelated_integrity_error_is_not_masked(self):
        serializer = MagicMock()
        serializer.validated_data = {
            "staff": self.staff,
            "date": date(2026, 7, 13),
            "end_time": None,
        }
        serializer.save.side_effect = IntegrityError("unrelated create constraint")
        view = WorkRecordViewSet()
        view.request = type("Request", (), {"tenant": self.tenant})()

        with self.assertRaisesMessage(IntegrityError, "unrelated create constraint"):
            view.perform_create(serializer)

    def test_direct_update_unrelated_integrity_error_is_not_masked(self):
        closed = WorkRecord.objects.create(
            tenant=self.tenant,
            staff=self.staff,
            work_type=self.work_type,
            date=date(2026, 7, 12),
            start_time=time(9, 0),
            end_time=time(18, 0),
        )
        serializer = MagicMock()
        serializer.instance = closed
        serializer.validated_data = {"end_time": None}
        serializer.save.side_effect = IntegrityError("unrelated update constraint")
        view = WorkRecordViewSet()

        with self.assertRaisesMessage(IntegrityError, "unrelated update constraint"):
            view.perform_update(serializer)

    def test_direct_work_record_api_returns_same_409(self):
        self._create_open_record()
        request = self.factory.post(
            "/api/v1/staffs/work-records/",
            {
                "staff": self.staff.id,
                "work_type": self.work_type.id,
                "date": "2026-07-13",
                "start_time": "10:00:00",
            },
            format="json",
        )
        request.tenant = self.tenant
        force_authenticate(request, user=self.owner)

        response = WorkRecordViewSet.as_view({"post": "create"})(request)

        self.assertEqual(response.status_code, 409, response.data)
        self.assertEqual(str(response.data["detail"]), "이미 근무 중입니다.")

    def test_reopening_closed_record_returns_409_when_another_is_open(self):
        closed = WorkRecord.objects.create(
            tenant=self.tenant,
            staff=self.staff,
            work_type=self.work_type,
            date=date(2026, 7, 12),
            start_time=time(9, 0),
            end_time=time(18, 0),
        )
        self._create_open_record()
        request = self.factory.patch(
            f"/api/v1/staffs/work-records/{closed.id}/",
            {"end_time": None},
            format="json",
        )
        request.tenant = self.tenant
        force_authenticate(request, user=self.owner)

        response = WorkRecordViewSet.as_view({"patch": "partial_update"})(request, pk=closed.id)

        self.assertEqual(response.status_code, 409, response.data)
        closed.refresh_from_db()
        self.assertEqual(closed.end_time, time(18, 0))

    def test_end_work_rejects_invalid_payroll_inputs_without_closing(self):
        invalid_payloads = [
            {"meal_minutes": "not-a-number"},
            {"meal_minutes": -1},
            {"adjustment_amount": "not-a-number"},
        ]
        for index, payload in enumerate(invalid_payloads, start=1):
            with self.subTest(payload=payload):
                record = WorkRecord.objects.create(
                    tenant=self.tenant,
                    staff=self.staff,
                    work_type=self.work_type,
                    date=date(2026, 7, 10 + index),
                    start_time=time(9, 0),
                )
                request = self.factory.post(
                    f"/api/v1/staffs/work-records/{record.id}/end_work/",
                    payload,
                    format="json",
                )
                request.tenant = self.tenant
                force_authenticate(request, user=self.owner)

                response = WorkRecordViewSet.as_view({"post": "end_work"})(
                    request,
                    pk=record.id,
                )

                self.assertEqual(response.status_code, 400, response.data)
                record.refresh_from_db()
                self.assertIsNone(record.end_time)

    def test_closed_records_do_not_block_new_clock_in(self):
        WorkRecord.objects.create(
            tenant=self.tenant,
            staff=self.staff,
            work_type=self.work_type,
            date=date(2026, 7, 12),
            start_time=time(9, 0),
            end_time=time(18, 0),
        )
        request = self.factory.post(
            f"/api/v1/staffs/{self.staff.id}/work-records/start-work/",
            {"work_type": self.work_type.id},
            format="json",
        )
        request.tenant = self.tenant
        force_authenticate(request, user=self.owner)

        response = StaffViewSet.as_view({"post": "start_work"})(request, pk=self.staff.id)

        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(
            WorkRecord.objects.filter(staff=self.staff, end_time__isnull=True).count(),
            1,
        )

    def test_update_cannot_move_record_into_target_staff_locked_month(self):
        target_user = User.objects.create_user(
            username="work-open-target",
            password="test1234",
            tenant=self.tenant,
        )
        target_staff = Staff.objects.create(
            tenant=self.tenant,
            user=target_user,
            name="마감 대상자",
        )
        WorkMonthLock.objects.create(
            tenant=self.tenant,
            staff=target_staff,
            year=2026,
            month=8,
            is_locked=True,
            locked_by=self.owner,
        )
        record = WorkRecord.objects.create(
            tenant=self.tenant,
            staff=self.staff,
            work_type=self.work_type,
            date=date(2026, 7, 13),
            start_time=time(9, 0),
            end_time=time(18, 0),
        )
        request = self.factory.patch(
            f"/api/v1/staffs/work-records/{record.id}/",
            {"staff": target_staff.id, "date": "2026-08-01"},
            format="json",
        )
        request.tenant = self.tenant
        force_authenticate(request, user=self.owner)

        response = WorkRecordViewSet.as_view({"patch": "partial_update"})(
            request,
            pk=record.id,
        )

        self.assertEqual(response.status_code, 400, response.data)
        record.refresh_from_db()
        self.assertEqual(record.staff_id, self.staff.id)
        self.assertEqual(record.date, date(2026, 7, 13))
