from __future__ import annotations

import threading
import unittest
import uuid

import pytest
from django.db import close_old_connections, connection
from django.test import TransactionTestCase

from apps.core.models import Tenant
from apps.domains.lectures.models import Lecture


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
