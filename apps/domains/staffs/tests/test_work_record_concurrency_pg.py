from __future__ import annotations

import threading
import unittest
import uuid
from datetime import date, time
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.db import close_old_connections, connection
from django.test import TransactionTestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.models import Tenant, TenantMembership
from apps.domains.staffs.models import Staff, WorkRecord, WorkType
from apps.domains.staffs.services import OpenWorkRecordConflict, start_work_record
from apps.domains.staffs.views import WorkRecordViewSet


pytestmark = pytest.mark.django_db(transaction=True)
User = get_user_model()


class WorkRecordConcurrencyPGTest(TransactionTestCase):
    @classmethod
    def setUpClass(cls):
        if connection.vendor != "postgresql":
            raise unittest.SkipTest("PostgreSQL is required for the clock-in concurrency test.")
        from django.apps import apps as django_apps

        cls.available_apps = [config.name for config in django_apps.get_app_configs()]
        super().setUpClass()

    def test_concurrent_shift_and_break_edits_validate_one_locked_state(self):
        suffix = uuid.uuid4().hex[:8]
        tenant = Tenant.objects.create(code=f"work-edit-{suffix}", name="QA work edit")
        user = User.objects.create_user(username=f"work-edit-{suffix}", tenant=tenant)
        TenantMembership.ensure_active(tenant=tenant, user=user, role="owner")
        staff = Staff.objects.create(tenant=tenant, name="QA staff")
        work_type = WorkType.objects.create(tenant=tenant, name="QA work", base_hourly_wage=15000)
        record = WorkRecord.objects.create(
            tenant=tenant, staff=staff, work_type=work_type, date=date(2026, 10, 1),
            end_date=date(2026, 10, 1), start_time=time(9), end_time=time(13),
        )
        barrier = threading.Barrier(2, timeout=10)
        original_get_object = WorkRecordViewSet.get_object
        outcomes = []
        errors = []

        def read_same_snapshot(view):
            instance = original_get_object(view)
            barrier.wait()
            return instance

        def worker(data):
            close_old_connections()
            try:
                request = APIRequestFactory().patch("/work-records/", data, format="json")
                request.tenant = tenant
                force_authenticate(request, user=user)
                response = WorkRecordViewSet.as_view({"patch": "partial_update"})(request, pk=record.pk)
                outcomes.append(response.status_code)
            except Exception as exc:
                errors.append(repr(exc))
            finally:
                close_old_connections()

        with patch.object(WorkRecordViewSet, "get_object", read_same_snapshot):
            threads = [threading.Thread(target=worker, args=(data,)) for data in (
                {"end_time": "11:00"}, {"break_minutes": 150},
            )]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=20)
        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(errors, [])
        self.assertCountEqual(outcomes, [200, 400])
        record.refresh_from_db()
        self.assertIn((record.end_time, record.break_minutes, record.amount), (
            (time(11), 0, 30000), (time(13), 150, 22500),
        ))

    def test_concurrent_clock_in_creates_one_open_record(self):
        suffix = uuid.uuid4().hex[:8]
        tenant = Tenant.objects.create(code=f"work-pg-{suffix}", name=f"Work PG {suffix}", is_active=True)
        user = User.objects.create_user(username=f"work-pg-user-{suffix}", tenant=tenant)
        staff = Staff.objects.create(tenant=tenant, user=user, name="동시근무자")
        work_type = WorkType.objects.create(
            tenant=tenant,
            name="동시근무",
            base_hourly_wage=10000,
            is_active=True,
        )
        barrier = threading.Barrier(2, timeout=10)
        outcomes: list[str] = []

        def worker():
            close_old_connections()
            try:
                thread_staff = Staff.objects.get(id=staff.id)
                barrier.wait()
                start_work_record(
                    staff=thread_staff,
                    work_type_id=work_type.id,
                    date=date(2026, 7, 13),
                    start_time=time(9, 0),
                )
                outcomes.append("created")
            except OpenWorkRecordConflict:
                outcomes.append("conflict")
            finally:
                close_old_connections()

        threads = [threading.Thread(target=worker) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=15)

        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertCountEqual(outcomes, ["created", "conflict"])
        self.assertEqual(
            WorkRecord.objects.filter(staff=staff, end_time__isnull=True).count(),
            1,
        )
