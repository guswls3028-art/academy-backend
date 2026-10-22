from __future__ import annotations

from datetime import timedelta
from io import StringIO
from unittest.mock import patch
from uuid import uuid4

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.core.models import OpsAuditLog, ProductUsageEvent, Program, Tenant
from apps.core.product_analytics.pilot import _postgres_storage, build_pilot_report


@override_settings(PRODUCT_ANALYTICS_HASH_KEY="pilot-test-key")
class ProductUsagePilotTests(TestCase):
    def setUp(self):
        self.pilot = Tenant.objects.create(
            code="hakwonplus",
            name="Internal Pilot",
            is_active=True,
        )
        self.program = Program.objects.get(tenant=self.pilot)
        self.program.feature_flags = {"product_usage_analytics_enabled": True}
        self.program.save(update_fields=["feature_flags"])

    def event(self, **overrides):
        values = {
            "event_id": uuid4(),
            "tenant": self.pilot,
            "actor_hash": "a" * 64,
            "role": "teacher",
            "audience_group": "teacher_staff",
            "session_id": uuid4(),
            "view_id": uuid4(),
            "event_type": "task_success",
            "feature_id": "scores.manage",
            "screen_id": "admin.scores.entry",
            "surface": "admin",
            "route_template": "/workspace/lectures/:lectureId/sessions/:sessionId/scores",
            "action_id": "scores.save",
            "device_class": "desktop",
            "client_release": "test-release",
            "catalog_version": "2026-08-12",
            "occurred_at": timezone.now() - timedelta(minutes=1),
        }
        values.update(overrides)
        return ProductUsageEvent.objects.create(**values)

    @patch("apps.core.product_analytics.pilot.connection")
    def test_zero_observation_storage_has_no_fabricated_projection(
        self, mock_connection
    ):
        mock_connection.vendor = "postgresql"
        cursor = mock_connection.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value = (130_301_952, 51_634_176, 3_741_416_807)

        storage = _postgres_storage(observed_days=0, projection_days=90)

        self.assertEqual(storage["raw_relation_bytes"], 130_301_952)
        self.assertEqual(storage["daily_relation_bytes"], 51_634_176)
        self.assertIsNone(storage["projected_analytics_bytes"])
        self.assertIsNone(storage["projected_database_share"])
        self.assertEqual(storage["projection_status"], "insufficient_observation")

        storage = _postgres_storage(observed_days=10, projection_days=90)
        self.assertEqual(storage["projected_analytics_bytes"], 855_613_440)
        self.assertEqual(storage["projected_database_share"], 0.228687)
        self.assertEqual(storage["projection_status"], "scope_unverified")

    @patch("apps.core.product_analytics.pilot._postgres_storage")
    def test_unverified_storage_projection_fails_without_disabling_pilot(
        self, mock_storage
    ):
        for projection_status, expected in (
            ("unavailable", "storage_projection_unavailable"),
            ("insufficient_observation", "storage_projection_unavailable"),
            ("scope_unverified", "storage_projection_scope_unverified"),
        ):
            with self.subTest(projection_status=projection_status):
                mock_storage.return_value = {
                    "raw_relation_bytes": 130_301_952,
                    "daily_relation_bytes": 51_634_176,
                    "database_bytes": 3_741_416_807,
                    "projected_analytics_bytes": 8_556_134_400,
                    "projected_database_share": 2.28687,
                    "projection_status": projection_status,
                }
                with self.assertRaisesMessage(CommandError, expected):
                    call_command(
                        "report_product_usage_pilot",
                        tenant_code="hakwonplus",
                        db_time_share=0.0,
                        write_share=0.0,
                        disable_on_hard_breach=True,
                        confirm="DISABLE hakwonplus ON HARD BREACH",
                        stdout=StringIO(),
                    )
                self.program.refresh_from_db()
                self.assertTrue(
                    self.program.feature_flags["product_usage_analytics_enabled"]
                )
                self.assertFalse(OpsAuditLog.objects.filter(
                    action="product_analytics.failsafe_disable"
                ).exists())

    def test_report_keeps_synthetic_and_impersonated_out_of_eligible_counts(self):
        self.event(event_type="task_start")
        self.event(event_type="task_success")
        self.event(synthetic=True)
        self.event(is_impersonated=True)

        report = build_pilot_report(
            tenant_code="hakwonplus",
            db_time_share=0.01,
            write_share=0.02,
            max_projected_database_share=1.0,
        )

        self.assertEqual(report["status"], "breach")
        self.assertEqual(report["auto_disable_reasons"], [])
        self.assertTrue(set(report["breaches"]) <= {
            "storage_projection_unavailable", "storage_projection_scope_unverified"
        })
        self.assertEqual(report["quality"]["raw_events"], 4)
        self.assertEqual(report["quality"]["eligible_events"], 2)
        self.assertEqual(report["quality"]["synthetic_events"], 1)
        self.assertEqual(report["quality"]["impersonated_events"], 1)
        self.assertEqual(report["tasks"]["completion_rate"], 1.0)

    @patch("apps.core.product_analytics.pilot._postgres_storage")
    def test_verified_storage_gate_passes_below_limit_and_disables_at_limit(self, mock_storage):
        storage = {"projection_status": "available", "projected_database_share": 0.19}
        mock_storage.return_value = storage
        call_command(
            "report_product_usage_pilot", tenant_code="hakwonplus",
            db_time_share=0.01, write_share=0.01, stdout=StringIO(),
        )
        self.program.refresh_from_db()
        self.assertTrue(self.program.feature_flags["product_usage_analytics_enabled"])
        storage["projected_database_share"] = 0.20
        with self.assertRaisesMessage(CommandError, "projected_database_share_exceeded"):
            call_command(
                "report_product_usage_pilot", tenant_code="hakwonplus",
                db_time_share=0.01, write_share=0.01, disable_on_hard_breach=True,
                confirm="DISABLE hakwonplus ON HARD BREACH", stdout=StringIO(),
            )
        self.program.refresh_from_db()
        self.assertFalse(self.program.feature_flags["product_usage_analytics_enabled"])
        self.assertEqual(OpsAuditLog.objects.get(
            action="product_analytics.failsafe_disable"
        ).payload["reasons"], ["projected_database_share_exceeded"])

    def test_write_share_breach_disables_even_with_unverified_storage(self):
        with self.assertRaisesMessage(CommandError, "write_share_exceeded"):
            call_command(
                "report_product_usage_pilot", tenant_code="hakwonplus",
                db_time_share=0.01, write_share=0.10, disable_on_hard_breach=True,
                confirm="DISABLE hakwonplus ON HARD BREACH", stdout=StringIO(),
            )
        self.program.refresh_from_db()
        self.assertFalse(self.program.feature_flags["product_usage_analytics_enabled"])
        self.assertEqual(OpsAuditLog.objects.get(
            action="product_analytics.failsafe_disable"
        ).payload["reasons"], ["write_share_exceeded"])

    def test_unexpected_enabled_tenant_fails_without_mutating_flags(self):
        other = Tenant.objects.create(code="other", name="Other", is_active=True)
        other_program = Program.objects.get(tenant=other)
        other_program.feature_flags = {"product_usage_analytics_enabled": True}
        other_program.save(update_fields=["feature_flags"])

        with self.assertRaisesMessage(CommandError, "enabled_tenant_scope_mismatch"):
            call_command(
                "report_product_usage_pilot",
                tenant_code="hakwonplus",
                db_time_share=0.01,
                write_share=0.01,
                max_projected_database_share=1.0,
                stdout=StringIO(),
            )

        self.program.refresh_from_db()
        other_program.refresh_from_db()
        self.assertTrue(self.program.feature_flags["product_usage_analytics_enabled"])
        self.assertTrue(other_program.feature_flags["product_usage_analytics_enabled"])

    def test_enabled_tenant_scope_breach_disables_exact_pilot(self):
        other = Tenant.objects.create(code="other", name="Other", is_active=True)
        other_program = Program.objects.get(tenant=other)
        other_program.feature_flags = {"product_usage_analytics_enabled": True}
        other_program.save(update_fields=["feature_flags"])

        with self.assertRaisesMessage(CommandError, "enabled_tenant_scope_mismatch"):
            call_command(
                "report_product_usage_pilot",
                tenant_code="hakwonplus",
                db_time_share=0.01,
                write_share=0.01,
                max_projected_database_share=1.0,
                disable_on_hard_breach=True,
                confirm="DISABLE hakwonplus ON HARD BREACH",
                stdout=StringIO(),
            )

        self.program.refresh_from_db()
        other_program.refresh_from_db()
        self.assertFalse(self.program.feature_flags["product_usage_analytics_enabled"])
        self.assertTrue(other_program.feature_flags["product_usage_analytics_enabled"])
        audit = OpsAuditLog.objects.get(
            action="product_analytics.failsafe_disable"
        )
        self.assertEqual(
            audit.payload["reasons"],
            ["enabled_tenant_scope_mismatch"],
        )

    def test_recent_nonpilot_event_disables_exact_pilot(self):
        other = Tenant.objects.create(code="other", name="Other", is_active=True)
        self.event(tenant=other)

        with self.assertRaisesMessage(CommandError, "recent_nonpilot_events"):
            call_command(
                "report_product_usage_pilot",
                tenant_code="hakwonplus",
                db_time_share=0.01,
                write_share=0.01,
                max_projected_database_share=1.0,
                disable_on_hard_breach=True,
                confirm="DISABLE hakwonplus ON HARD BREACH",
                stdout=StringIO(),
            )

        self.program.refresh_from_db()
        self.assertFalse(self.program.feature_flags["product_usage_analytics_enabled"])
        audit = OpsAuditLog.objects.get(
            action="product_analytics.failsafe_disable"
        )
        self.assertEqual(audit.payload["reasons"], ["recent_nonpilot_events"])

    def test_hard_performance_breach_disables_exact_pilot_and_audits(self):
        with self.assertRaisesMessage(CommandError, "db_time_share_exceeded"):
            call_command(
                "report_product_usage_pilot",
                tenant_code="hakwonplus",
                db_time_share=0.11,
                write_share=0.01,
                max_projected_database_share=1.0,
                disable_on_hard_breach=True,
                confirm="DISABLE hakwonplus ON HARD BREACH",
                stdout=StringIO(),
            )

        self.program.refresh_from_db()
        self.assertFalse(self.program.feature_flags["product_usage_analytics_enabled"])
        audit = OpsAuditLog.objects.get(
            action="product_analytics.failsafe_disable"
        )
        self.assertEqual(audit.target_tenant_id, self.pilot.id)
        self.assertEqual(audit.payload["reasons"], ["db_time_share_exceeded"])

    def test_hard_breach_requires_exact_confirmation_before_mutation(self):
        with self.assertRaisesMessage(CommandError, "exact documented confirmation"):
            call_command(
                "report_product_usage_pilot",
                tenant_code="hakwonplus",
                db_time_share=0.11,
                write_share=0.01,
                max_projected_database_share=1.0,
                disable_on_hard_breach=True,
                confirm="yes",
            )

        self.program.refresh_from_db()
        self.assertTrue(self.program.feature_flags["product_usage_analytics_enabled"])
