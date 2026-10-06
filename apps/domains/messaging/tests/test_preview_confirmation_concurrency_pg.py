from concurrent.futures import ThreadPoolExecutor
import threading
from unittest.mock import patch

from django.db import close_old_connections, connection
from django.test import TransactionTestCase

from apps.domains.messaging.models import MessageTemplate, ScheduledNotification
from apps.domains.messaging.notification_dispatch import consume_preview_token_and_execute
from apps.domains.messaging.serializers import MessageTemplateSerializer
from apps.domains.messaging.tests import test_notification_preview_views as fixtures


class PreviewConfirmationConcurrencyTests(TransactionTestCase):
    def setUp(self):
        if connection.vendor != "postgresql":
            self.skipTest("PostgreSQL row-lock concurrency contract")
        fixtures.NotificationPreviewConfirmDurabilityTests.setUp(self)

    _token = fixtures.NotificationPreviewConfirmDurabilityTests._token

    def _parallel(self, operation):
        barrier = threading.Barrier(2)

        def run(index):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                return operation(index)
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(run, index) for index in range(2)]
            return [future.result(timeout=15) for future in futures]

    @patch("apps.domains.messaging.policy.check_recipient_allowed", return_value=True)
    @patch("apps.domains.messaging.scheduled.process_due_notifications")
    def test_simultaneous_confirmations_share_one_receipt_and_dispatch(self, dispatch, _allowed):
        token = self._token()
        results = self._parallel(lambda _index: consume_preview_token_and_execute(
            str(token.token), self.tenant, session_type="manual",
        ))
        self.assertTrue(all("batch_result" in result for result in results))
        self.assertEqual(results[0]["batch_result"]["batch_id"], results[1]["batch_result"]["batch_id"])
        self.assertEqual(ScheduledNotification.objects.filter(tenant=self.tenant).count(), 1)
        dispatch.assert_called_once()

    def test_simultaneous_default_creations_commit_without_losing_user_content(self):
        def create(index):
            serializer = MessageTemplateSerializer(data={
                "name": f"동시 기본 {index}", "body": f"보존 문구 {index}",
                "category": "grades", "is_user_default": True,
            })
            serializer.is_valid(raise_exception=True)
            return serializer.save(tenant=self.tenant).pk

        created = self._parallel(create)
        rows = MessageTemplate.objects.filter(tenant=self.tenant, pk__in=created)
        self.assertEqual(rows.count(), 2)
        self.assertEqual(rows.filter(is_user_default=True).count(), 1)
        self.assertEqual(set(rows.values_list("body", flat=True)), {"보존 문구 0", "보존 문구 1"})
