from __future__ import annotations

from importlib import import_module

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase, override_settings
from django.utils import timezone


@override_settings(OWNER_TENANT_ID=1)
class MessagingDeliveryStateMigrationTests(TransactionTestCase):
    migrate_from = ("messaging", "0032_notificationlog_source_tenant_fk")
    migrate_to = ("messaging", "0035_finalize_messaging_delivery_state")

    def setUp(self):
        super().setUp()
        executor = MigrationExecutor(connection)
        executor.migrate([self.migrate_from])
        old_apps = executor.loader.project_state([self.migrate_from]).apps
        Tenant = old_apps.get_model("core", "Tenant")
        NotificationLog = old_apps.get_model("messaging", "NotificationLog")
        ScheduledNotification = old_apps.get_model("messaging", "ScheduledNotification")
        self.old_scheduled_notification = ScheduledNotification
        tenant = Tenant.objects.create(
            id=1,
            code="migration-delivery",
            name="Migration Delivery",
            is_active=True,
        )
        NotificationLog.objects.create(
            tenant=tenant,
            success=False,
            status="sent",
            message_mode="alimtalk",
        )
        ScheduledNotification.objects.create(
            tenant=tenant,
            trigger="migration_test",
            send_at="2026-07-13T00:00:00Z",
            payload={
                "tenant_id": tenant.id,
                "to": "01011112222",
                "text": "migration",
                "message_mode": "alimtalk",
            },
        )
        ScheduledNotification.objects.create(
            tenant=tenant,
            trigger="registration_approved_parent",
            send_at="2026-07-13T00:00:00Z",
            status="sent",
            payload={
                "tenant_id": tenant.id,
                "to": "01031217466",
                "text": "임시 비밀번호: secret-1234",
                "message_mode": "alimtalk",
                "event_type": "registration_approved_parent",
                "target_type": "account",
                "target_id": "parent:17:01031217466",
            },
        )

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        super().tearDown()

    def test_forward_migration_backfills_and_is_idempotent(self):
        executor = MigrationExecutor(connection)
        executor.migrate([self.migrate_to])
        apps = executor.loader.project_state([self.migrate_to]).apps
        NotificationLog = apps.get_model("messaging", "NotificationLog")
        ScheduledNotification = apps.get_model("messaging", "ScheduledNotification")

        self.assertEqual(NotificationLog.objects.get().status, "failed")
        notification = ScheduledNotification.objects.get(trigger="migration_test")
        self.assertIsNotNone(notification.dispatch_key)
        self.assertTrue(notification.business_idempotency_key)
        self.assertTrue(notification.payload["occurrence_key"].startswith("dispatch:"))
        terminal = ScheduledNotification.objects.get(
            trigger="registration_approved_parent"
        )
        self.assertEqual(terminal.payload["redacted"], True)
        self.assertEqual(terminal.payload["target_id"], "parent:17")
        self.assertNotIn("01031217466", str(terminal.payload))
        self.assertNotIn("secret-1234", str(terminal.payload))

        migration = import_module(
            "apps.domains.messaging.migrations.0034_backfill_messaging_delivery_state"
        )
        with connection.schema_editor() as schema_editor:
            migration.backfill_delivery_state(apps, schema_editor)

        notification.refresh_from_db()
        self.assertTrue(notification.business_idempotency_key)

        # Keep using the pre-0033 historical model after the schema has moved
        # forward. This emits the exact INSERT an old Django binary would send,
        # including all of its existing NOT NULL fields while omitting new ones.
        self.old_scheduled_notification.objects.create(
            tenant_id=1,
            trigger="old_binary_insert",
            send_at=timezone.now(),
            payload={
                "tenant_id": 1,
                "to": "01011112222",
                "text": "old binary",
                "message_mode": "alimtalk",
            },
        )
        old_binary = ScheduledNotification.objects.get(trigger="old_binary_insert")
        self.assertIsNone(old_binary.dispatch_key)
        self.assertEqual(old_binary.attempt_count, 0)
        self.assertEqual(old_binary.business_idempotency_key, "")

        new_binary = ScheduledNotification.objects.create(
            tenant_id=1,
            trigger="new_binary_insert",
            send_at=timezone.now(),
            payload={
                "tenant_id": 1,
                "to": "01011112222",
                "text": "new binary",
                "message_mode": "alimtalk",
            },
        )
        self.assertIsNotNone(new_binary.dispatch_key)

    def test_migration_fails_without_overwriting_malformed_payload(self):
        malformed = self.old_scheduled_notification.objects.create(
            tenant_id=1,
            trigger="malformed_payload",
            send_at=timezone.now(),
            payload=["legacy", "forensic", "payload"],
        )
        executor = MigrationExecutor(connection)

        with self.assertRaisesRegex(
            RuntimeError,
            f"ids=\\[{malformed.id}\\]",
        ):
            executor.migrate(
                [("messaging", "0034_backfill_messaging_delivery_state")]
            )

        malformed.refresh_from_db()
        self.assertEqual(malformed.payload, ["legacy", "forensic", "payload"])
        malformed.delete()
