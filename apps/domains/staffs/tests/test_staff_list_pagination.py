from datetime import date, datetime, time, timezone

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.core.models import Tenant, TenantMembership
from apps.domains.staffs.models import (
    ExpenseRecord, PayrollSnapshot, Staff, StaffWorkType,
    WorkMonthLock, WorkRecord, WorkType,
)


@override_settings(
    ALLOWED_HOSTS=["api.hakwonplus.com"],
    TENANT_HEADER_CODE_ALLOWED_HOSTS=("api.hakwonplus.com",),
    BILLING_TEST_BYPASS_SUBSCRIPTION=True,
)
class StaffListPaginationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(name="QA staff pages", code="qa-staff-pages")
        cls.other = Tenant.objects.create(name="QA other pages", code="qa-other-pages")
        cls.owner = get_user_model().objects.create_user(username="qa-page-owner", password="qa-password")
        TenantMembership.ensure_active(tenant=cls.tenant, user=cls.owner, role="owner")
        cls.staffs = [Staff.objects.create(tenant=cls.tenant, name="동명이인") for _ in range(7)]
        cls.types = [WorkType.objects.create(tenant=cls.tenant, name="같은 유형", base_hourly_wage=12000) for _ in range(7)]
        cls.assignments = [StaffWorkType.objects.create(tenant=cls.tenant, staff=cls.staffs[0], work_type=kind) for kind in cls.types]
        cls.records = [WorkRecord.objects.create(
            tenant=cls.tenant, staff=cls.staffs[0], work_type=cls.types[0], date=date(2026, 10, 1),
            start_time=time(9), end_time=time(10), work_hours=1, amount=12000,
            resolved_hourly_wage=12000,
        ) for _ in range(7)]
        cls.expenses = [ExpenseRecord.objects.create(
            tenant=cls.tenant, staff=cls.staffs[0], date=date(2026, 10, 1), title="검증 비용", amount=1000,
        ) for _ in range(7)]
        cls.locks = [WorkMonthLock.objects.create(tenant=cls.tenant, staff=staff, year=2026, month=10) for staff in cls.staffs]
        cls.snapshots = [PayrollSnapshot.objects.create(tenant=cls.tenant, staff=staff, year=2026, month=10) for staff in cls.staffs]
        for model in (Staff, WorkType, StaffWorkType, WorkRecord, ExpenseRecord):
            model.objects.filter(tenant=cls.tenant).update(created_at=datetime(2026, 10, 1, tzinfo=timezone.utc))

        foreign_staff = Staff.objects.create(tenant=cls.other, name="동명이인")
        foreign_type = WorkType.objects.create(tenant=cls.other, name="같은 유형", base_hourly_wage=12000)
        StaffWorkType.objects.create(tenant=cls.other, staff=foreign_staff, work_type=foreign_type)
        WorkRecord.objects.create(
            tenant=cls.other, staff=foreign_staff, work_type=foreign_type,
            date=date(2026, 10, 1), start_time=time(9), end_time=time(10), work_hours=1, amount=12000,
        )
        ExpenseRecord.objects.create(tenant=cls.other, staff=foreign_staff, date=date(2026, 10, 1), title="외부 비용", amount=1000)
        WorkMonthLock.objects.create(tenant=cls.other, staff=foreign_staff, year=2026, month=10)
        PayrollSnapshot.objects.create(tenant=cls.other, staff=foreign_staff, year=2026, month=10)

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(self.owner)

    def read_pages(self, endpoint, params):
        ids = []
        for page in range(1, 5):
            response = self.client.get(
                f"/api/v1/staffs/{endpoint}", {**params, "page": page, "page_size": 2},
                HTTP_HOST="api.hakwonplus.com", HTTP_X_TENANT_CODE=self.tenant.code,
            )
            self.assertEqual(response.status_code, 200, response.data)
            self.assertEqual(response.data["count"], 7)
            ids.extend(row["id"] for row in response.data["results"])
            self.assertEqual(response.data["next"] is None, page == 4)
        self.assertEqual(len(set(ids)), 7)
        return ids

    def test_equal_keys_have_stable_descending_pages_without_other_tenant_rows(self):
        cases = (
            ("", {"ordering": "-name"}, self.staffs),
            ("work-types/", {"ordering": "-name"}, self.types),
            ("staff-work-types/", {"ordering": "-created_at"}, self.assignments),
            ("work-records/", {}, self.records),
            ("expense-records/", {"ordering": "-date"}, self.expenses),
            ("work-month-locks/", {"year": 2026, "month": 10}, self.locks),
            ("payroll-snapshots/", {"year": 2026, "month": 10}, self.snapshots),
            (f"{self.staffs[0].id}/work-records/", {"date_from": "2026-10-01", "date_to": "2026-10-31"}, self.records),
        )
        for endpoint, params, rows in cases:
            with self.subTest(endpoint=endpoint):
                expected = sorted((row.id for row in rows), reverse=True)
                self.assertEqual(self.read_pages(endpoint, params), expected)
                self.assertEqual(self.read_pages(endpoint, params), expected)

    def test_equal_name_ascending_keeps_the_same_complete_roster(self):
        self.assertEqual(self.read_pages("", {"ordering": "name"}), sorted(row.id for row in self.staffs))
