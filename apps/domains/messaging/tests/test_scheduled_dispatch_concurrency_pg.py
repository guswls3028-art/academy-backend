from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from django.db import close_old_connections, connection
from django.test import TransactionTestCase
from django.utils import timezone

from apps.core.models import Tenant
from apps.domains.messaging.models import ScheduledNotification
from apps.domains.messaging.scheduled import process_due_notifications


class ScheduledDispatchConcurrencyPostgresTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        if connection.vendor != "postgresql":
            self.skipTest("PostgreSQL row-lock concurrency contract")
        self.tenant = Tenant.objects.create(
            code="msg-dispatch-pg",
            name="Messaging Dispatch PG",
            is_active=True,
        )

    @staticmethod
    def _process() -> dict:
        close_old_connections()
        try:
            return process_due_notifications(batch_size=1)
        finally:
            close_old_connections()

    def test_two_drainers_enqueue_one_due_dispatch_once(self):
        notification = ScheduledNotification.objects.create(
            tenant=self.tenant,
            trigger="clinic_reminder",
            send_at=timezone.now(),
            payload={
                "tenant_id": self.tenant.id,
                "to": "01031217466",
                "text": "동시성 검증",
                "message_mode": "alimtalk",
            },
        )
        provider_entered = threading.Event()
        provider_release = threading.Event()
        calls: list[str] = []
        calls_lock = threading.Lock()

        def blocking_enqueue(**kwargs):
            with calls_lock:
                calls.append(kwargs["occurrence_key"])
            provider_entered.set()
            self.assertTrue(provider_release.wait(timeout=5))
            return True

        with patch(
            "apps.domains.messaging.services.enqueue_sms",
            side_effect=blocking_enqueue,
        ):
            with ThreadPoolExecutor(max_workers=2) as pool:
                first = pool.submit(self._process)
                self.assertTrue(provider_entered.wait(timeout=5))
                second = pool.submit(self._process)
                second_result = second.result(timeout=5)
                provider_release.set()
                first_result = first.result(timeout=5)

        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0], f"dispatch:{notification.dispatch_key.hex}")
        self.assertEqual(first_result["sent"], 1)
        self.assertEqual(second_result["processed"], 0)
        notification.refresh_from_db()
        self.assertEqual(notification.status, ScheduledNotification.Status.SENT)

    def test_concurrent_immediate_reservations_cannot_oversubscribe_hourly_quota(self):
        barrier = threading.Barrier(2)
        for index in range(2):
            ScheduledNotification.objects.create(
                tenant=self.tenant,
                trigger="quota_concurrency",
                send_at=timezone.now(),
                payload={
                    "tenant_id": self.tenant.id,
                    "to": f"0100000000{index}",
                    "text": "quota",
                    "message_mode": "alimtalk",
                },
            )

        def drain() -> dict:
            close_old_connections()
            try:
                barrier.wait(timeout=5)
                return process_due_notifications(batch_size=1)
            finally:
                close_old_connections()

        with (
            patch("apps.domains.messaging.scheduled.HOURLY_SEND_LIMIT", 1),
            patch("apps.domains.messaging.services.enqueue_sms", return_value=True),
        ):
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(lambda _index: drain(), [1, 2]))

        self.assertEqual(sum(result["sent"] for result in results), 1)
        self.assertEqual(sum(result["deferred"] for result in results), 1)
        self.assertEqual(
            ScheduledNotification.objects.filter(
                tenant=self.tenant,
                status=ScheduledNotification.Status.SENT,
            ).count(),
            1,
        )
