from __future__ import annotations

import threading
import unittest
import uuid
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.db import DatabaseError, close_old_connections, connection, transaction
from django.test import TransactionTestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.models import Tenant, TenantMembership
from apps.domains.lectures.models import Lecture
from apps.domains.lectures.views import LectureViewSet, first_lecture_delete_blocker


pytestmark = pytest.mark.django_db(transaction=True)


class LectureOrderConcurrencyPostgresTests(TransactionTestCase):
    @classmethod
    def setUpClass(cls):
        if connection.vendor != "postgresql":
            raise unittest.SkipTest(
                "PostgreSQL is required for lecture order concurrency."
            )
        from django.apps import apps as django_apps

        cls.available_apps = [config.name for config in django_apps.get_app_configs()]
        super().setUpClass()

    def test_concurrent_creates_receive_distinct_orders(self):
        suffix = uuid.uuid4().hex[:8]
        tenant = Tenant.objects.create(
            code=f"lecture-order-pg-{suffix}",
            name=f"Lecture order PG {suffix}",
            is_active=True,
        )
        barrier = threading.Barrier(2, timeout=10)
        errors: list[Exception] = []

        def worker(index: int):
            close_old_connections()
            try:
                barrier.wait()
                Lecture.objects.create(
                    tenant_id=tenant.id,
                    title=f"Concurrent lecture {index}",
                    name="Teacher",
                    subject="math",
                )
            except Exception as exc:  # pragma: no cover - surfaced below
                errors.append(exc)
            finally:
                close_old_connections()

        threads = [threading.Thread(target=worker, args=(index,)) for index in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=15)

        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(errors, [])
        self.assertEqual(
            list(
                Lecture.objects.filter(tenant=tenant)
                .order_by("display_order")
                .values_list("display_order", flat=True)
            ),
            [1, 2],
        )

    def test_delete_locks_inactive_qr_before_checking_protected_records(self):
        tenant = Tenant.objects.create(code="lecture-delete-pg", name="Lecture delete PG")
        lecture = Lecture.objects.create(
            tenant=tenant, title="Lecture", name="Teacher", subject="math",
        )
        admin = get_user_model().objects.create_user(
            username="lecture-delete-pg", tenant=tenant, is_staff=True,
        )
        TenantMembership.ensure_active(tenant=tenant, user=admin, role="admin")
        point = lecture.attendanceqrpoint_set.create(
            tenant=tenant, label="Inactive QR", kind="class", active=False,
        )
        reached_preflight = threading.Event()
        allow_delete = threading.Event()
        outcomes = []

        def check_blockers(locked_lecture):
            reached_preflight.set()
            if not allow_delete.wait(timeout=10):
                raise RuntimeError("Lecture deletion preflight was not released")
            return first_lecture_delete_blocker(locked_lecture)

        def delete_lecture():
            close_old_connections()
            try:
                request = APIRequestFactory().delete(f"/api/v1/lectures/lectures/{lecture.pk}/")
                request.tenant = tenant
                force_authenticate(request, user=admin)
                response = LectureViewSet.as_view({"delete": "destroy"})(request, pk=lecture.pk)
                outcomes.append(response.status_code)
            except Exception as exc:  # pragma: no cover - surfaced below
                outcomes.append(exc)
            finally:
                close_old_connections()

        with patch("apps.domains.lectures.views.first_lecture_delete_blocker", check_blockers):
            thread = threading.Thread(target=delete_lecture)
            thread.start()
            try:
                self.assertTrue(reached_preflight.wait(timeout=10))
                with self.assertRaises(DatabaseError) as locked, transaction.atomic():
                    lecture.attendanceqrpoint_set.select_for_update(nowait=True).get(pk=point.pk)
                cause = locked.exception.__cause__
                self.assertEqual(getattr(cause, "sqlstate", getattr(cause, "pgcode", None)), "55P03")
            finally:
                allow_delete.set()
                thread.join(timeout=15)

        self.assertFalse(thread.is_alive())
        self.assertEqual(outcomes, [204])
