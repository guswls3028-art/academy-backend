from __future__ import annotations

import json
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.core.management import call_command
from django.http import JsonResponse
from django.test import RequestFactory, TestCase, override_settings

from apps.core.models import Tenant
from apps.core.observability.tenant_db_usage import (
    TenantDatabaseUsageMiddleware,
)


class TenantDatabaseUsageTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(
            code="db-usage",
            name="DB Usage",
            is_active=True,
        )

    @override_settings(
        TENANT_DB_USAGE_ENABLED=True,
        TENANT_DB_USAGE_SAMPLE_RATE=1.0,
        TENANT_DB_USAGE_SLOW_REQUEST_MS=10_000,
    )
    def test_logs_counts_and_duration_without_sql_or_user_data(self):
        def get_response(request):
            Tenant.objects.filter(id=self.tenant.id).exists()
            return JsonResponse({"ok": True})

        request = RequestFactory().get("/api/v1/core/program/")
        request.tenant = self.tenant
        with (
            patch(
                "apps.core.observability.tenant_db_usage.random.random",
                return_value=0,
            ),
            self.assertLogs("academy.tenant_db_usage", level="INFO") as captured,
        ):
            response = TenantDatabaseUsageMiddleware(get_response)(request)

        self.assertEqual(response.status_code, 200)
        record = captured.records[0]
        self.assertEqual(record.event, "tenant_db_usage")
        self.assertEqual(record.tenant_id, self.tenant.id)
        self.assertGreaterEqual(record.query_count, 1)
        self.assertFalse(hasattr(record, "sql"))
        self.assertFalse(hasattr(record, "params"))
        self.assertFalse(hasattr(record, "user_id"))

    def test_report_command_summarizes_weighted_tenant_share(self):
        rows = [
            {
                "extra": {
                    "event": "tenant_db_usage",
                    "tenant_id": self.tenant.id,
                    "sample_weight": 10,
                    "query_count": 2,
                    "write_query_count": 1,
                    "db_duration_ms": 20,
                    "request_or_job_duration_ms": 50,
                }
            },
            {
                "extra": {
                    "event": "tenant_db_usage",
                    "tenant_id": 999,
                    "sample_weight": 1,
                    "query_count": 1,
                    "write_query_count": 0,
                    "db_duration_ms": 50,
                    "request_or_job_duration_ms": 80,
                }
            },
        ]
        with TemporaryDirectory() as directory:
            path = Path(directory) / "usage.jsonl"
            path.write_text(
                "\n".join(json.dumps(row) for row in rows),
                encoding="utf-8",
            )
            stdout = StringIO()
            call_command(
                "report_tenant_db_capacity",
                input=[str(path)],
                stdout=stdout,
            )

        report = json.loads(stdout.getvalue())
        self.assertEqual(report["tenant_count"], 2)
        self.assertEqual(report["tenants"][0]["tenant_id"], self.tenant.id)
        self.assertEqual(report["tenants"][0]["db_duration_ms"], 200.0)
        self.assertEqual(report["tenants"][0]["db_time_share"], 0.8)
