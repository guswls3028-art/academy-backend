from __future__ import annotations

from datetime import timedelta
from io import StringIO
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.http import HttpRequest, JsonResponse
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.api.common.middleware import UnhandledExceptionMiddleware
from apps.api.common import middleware as incident_middleware
from apps.api.common.throttles import UserIncidentReportThrottle
from apps.core.management.commands.check_dev_alerts import (
    CONTROLLED_OPS_PHONE,
    SMS_DELIVERY_ACTION,
    SMS_MAX_RECONCILIATIONS_PER_RUN,
    SMS_RATE_LIMIT_ACTION,
    _build_user_incident_sms,
    _reconcile_unresolved_sms_attempts,
    _send_ops_sms,
    rule_user_incidents,
)
from apps.core.models import OpsAuditLog, Tenant, TenantMembership, User
from apps.core.views.user_incidents import (
    UserIncidentReportView,
    sanitize_incident_route,
)


class UserIncidentReportViewTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(
            code="incident-tenant",
            name="Incident Tenant",
            is_active=True,
        )
        self.user = User.objects.create_user(
            username="incident-owner",
            password="1234",
            tenant=self.tenant,
        )
        TenantMembership.objects.create(
            tenant=self.tenant,
            user=self.user,
            role="owner",
            is_active=True,
        )
        self.factory = APIRequestFactory()

    def test_authenticated_member_report_is_tenant_scoped_and_sanitized(self):
        request = self.factory.post(
            "/api/v1/core/problem-reports/",
            {
                "source": "manual",
                "message": "학생 목록 저장 버튼이 동작하지 않습니다.",
                "route": "https://example.test/admin/students/123?phone=01012345678",
                "screen_size": "1366x768",
            },
            format="json",
        )
        request.tenant = self.tenant
        force_authenticate(request, user=self.user)

        response = UserIncidentReportView.as_view()(request)

        self.assertEqual(response.status_code, 201)
        log = OpsAuditLog.objects.get(pk=response.data["incident_id"])
        self.assertEqual(log.action, "user_incident.manual")
        self.assertEqual(log.target_tenant, self.tenant)
        self.assertEqual(log.actor_user, self.user)
        self.assertEqual(log.payload["route"], "/admin/students/:id")
        self.assertNotIn("phone", log.payload["route"])

    def test_non_member_is_denied(self):
        other_tenant = Tenant.objects.create(
            code="other-tenant",
            name="Other Tenant",
            is_active=True,
        )
        request = self.factory.post(
            "/api/v1/core/problem-reports/",
            {"source": "manual", "message": "문제", "route": "/admin"},
            format="json",
        )
        request.tenant = other_tenant
        force_authenticate(request, user=self.user)

        response = UserIncidentReportView.as_view()(request)

        self.assertEqual(response.status_code, 403)
        self.assertFalse(OpsAuditLog.objects.filter(action="user_incident.manual").exists())

    def test_frontend_exception_requires_error_name(self):
        request = self.factory.post(
            "/api/v1/core/problem-reports/",
            {
                "source": "frontend_exception",
                "message": "화면 오류",
                "route": "/admin/dashboard",
            },
            format="json",
        )
        request.tenant = self.tenant
        force_authenticate(request, user=self.user)

        response = UserIncidentReportView.as_view()(request)

        self.assertEqual(response.status_code, 400)

    def test_route_sanitizer_removes_ids_uuid_and_query(self):
        value = (
            "https://example.test/results/42/"
            "123e4567-e89b-12d3-a456-426614174000?student=홍길동"
        )
        self.assertEqual(
            sanitize_incident_route(value),
            "/results/:id/:uuid",
        )

    def test_manual_and_automatic_reports_use_separate_throttle_buckets(self):
        manual = SimpleNamespace(
            tenant=self.tenant,
            user=self.user,
            data={"source": "manual"},
        )
        automatic = SimpleNamespace(
            tenant=self.tenant,
            user=self.user,
            data={"source": "frontend_exception"},
        )

        throttle = UserIncidentReportThrottle()
        for _index in range(12):
            OpsAuditLog.objects.create(
                actor_user=self.user,
                target_tenant=self.tenant,
                action="user_incident.frontend_exception",
            )
        self.assertTrue(throttle.allow_request(manual, UserIncidentReportView()))
        self.assertFalse(throttle.allow_request(automatic, UserIncidentReportView()))

    def test_top_level_json_array_is_validation_error_not_server_error(self):
        request = self.factory.post(
            "/api/v1/core/problem-reports/",
            [],
            format="json",
        )
        request.tenant = self.tenant
        force_authenticate(request, user=self.user)

        response = UserIncidentReportView.as_view()(request)

        self.assertEqual(response.status_code, 400)


class BackendIncidentCaptureTests(TransactionTestCase):
    def setUp(self):
        incident_middleware._user_incident_sampled_at.clear()
        self.tenant = Tenant.objects.create(
            code="capture-tenant",
            name="Capture Tenant",
            is_active=True,
        )

    def _request(self, path="/api/v1/results/42/"):
        request = HttpRequest()
        request.method = "POST"
        request.path = path
        request.META["HTTP_USER_AGENT"] = "test-agent"
        request.tenant = self.tenant
        request.user = SimpleNamespace(is_authenticated=False)
        request.resolver_match = SimpleNamespace(route="api/v1/results/<int:pk>/")
        return request

    def test_5xx_response_records_metadata_only_incident(self):
        middleware = UnhandledExceptionMiddleware(
            get_response=lambda _request: JsonResponse({"detail": "error"}, status=500)
        )

        response = middleware(self._request())
        incident_middleware._user_incident_audit_queue.join()

        self.assertEqual(response.status_code, 500)
        log = OpsAuditLog.objects.get(action="user_incident.backend_5xx")
        self.assertEqual(log.target_tenant, self.tenant)
        self.assertEqual(log.payload["status"], 500)
        self.assertEqual(log.payload["route"], "api/v1/results/<int:pk>/")
        self.assertNotIn("body", log.payload)

    def test_health_5xx_is_not_recorded_as_user_incident(self):
        middleware = UnhandledExceptionMiddleware(
            get_response=lambda _request: JsonResponse({"detail": "error"}, status=500)
        )

        middleware(self._request("/health"))
        incident_middleware._user_incident_audit_queue.join()

        self.assertFalse(
            OpsAuditLog.objects.filter(action="user_incident.backend_5xx").exists()
        )

    def test_repeated_identical_5xx_is_sampled_before_database_write(self):
        middleware = UnhandledExceptionMiddleware(
            get_response=lambda _request: JsonResponse({"detail": "error"}, status=500)
        )
        middleware(self._request())
        middleware(self._request())
        incident_middleware._user_incident_audit_queue.join()

        self.assertEqual(
            OpsAuditLog.objects.filter(action="user_incident.backend_5xx").count(),
            1,
        )

    def test_identical_5xx_for_different_tenants_is_not_cross_suppressed(self):
        second_tenant = Tenant.objects.create(
            code="capture-tenant-2",
            name="Capture Tenant 2",
            is_active=True,
        )
        middleware = UnhandledExceptionMiddleware(
            get_response=lambda _request: JsonResponse({"detail": "error"}, status=500)
        )
        first = self._request()
        second = self._request()
        second.tenant = second_tenant
        middleware(first)
        middleware(second)
        incident_middleware._user_incident_audit_queue.join()

        self.assertEqual(
            OpsAuditLog.objects.filter(action="user_incident.backend_5xx").count(),
            2,
        )


class UserIncidentAlertRuleTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(
            code="alert-tenant",
            name="Alert Tenant",
            is_active=True,
        )

    def _backend_incident(self, *, tenant=None, status=500):
        return OpsAuditLog.objects.create(
            action="user_incident.backend_5xx",
            summary="GET route returned 500",
            target_tenant=tenant or self.tenant,
            payload={
                "source": "backend_5xx",
                "route": "api/v1/results/<int:pk>/",
                "method": "GET",
                "status": status,
                "exception_name": "ValueError",
            },
            result="failed",
        )

    def test_repeated_same_error_is_grouped_and_sms_is_short(self):
        self._backend_incident()
        self._backend_incident()

        result = rule_user_incidents()

        self.assertIsNotNone(result)
        self.assertEqual(result["total"], 2)
        self.assertEqual(len(result["rows"]), 1)
        self.assertEqual(result["rows"][0]["count"], 2)
        sms = _build_user_incident_sms(result)
        self.assertLessEqual(len(sms.encode("utf-8")), 90)
        self.assertIn(f"{self.tenant.code}#{self.tenant.id}", sms)
        self.assertIn("서버500", sms)
        self.assertNotIn("ValueError", sms)
        self.assertNotIn("api/v1/results", sms)

    def test_sms_identifies_multiple_tenants_with_controlled_reasons(self):
        second_tenant = Tenant.objects.create(
            code="second",
            name="Second Tenant",
            is_active=True,
        )
        self._backend_incident()
        OpsAuditLog.objects.create(
            action="user_incident.frontend_exception",
            target_tenant=second_tenant,
            payload={
                "route": "/admin/students/secret-name",
                "error_name": "UserSuppliedError",
                "message": "학생 홍길동 01012345678",
            },
            result="failed",
        )

        sms = _build_user_incident_sms(rule_user_incidents())

        self.assertIn("2곳", sms)
        self.assertIn(f"{self.tenant.code}#{self.tenant.id}:서버500", sms)
        self.assertIn(f"{second_tenant.code}#{second_tenant.id}:화면오류", sms)
        self.assertNotIn("secret-name", sms)
        self.assertNotIn("UserSuppliedError", sms)
        self.assertNotIn("홍길동", sms)
        self.assertNotIn("01012345678", sms)
        self.assertLessEqual(len(sms.encode("utf-8")), 90)

    def test_sms_sanitizes_hostile_tenant_metadata_and_reports_overflow(self):
        self.tenant.name = "악성\n010-3121-7466 010 9999 8888\u202e학원"
        self.tenant.save(update_fields=["name"])
        self._backend_incident()
        for index in range(8):
            tenant = Tenant.objects.create(
                code=f"overflow-{index}",
                name=f"아주긴테넌트이름{index}",
                is_active=True,
            )
            self._backend_incident(tenant=tenant)

        result = rule_user_incidents()
        sms = _build_user_incident_sms(result)

        self.assertIn(f"{self.tenant.code}#{self.tenant.id}", sms)
        self.assertRegex(sms, r"\+\d+곳")
        self.assertNotIn("악성", sms)
        self.assertNotIn("010-3121-7466", sms)
        self.assertNotIn("010 9999 8888", sms)
        self.assertNotIn("\u202e", sms)
        self.assertEqual(sms.count("\n"), 2)
        self.assertTrue(sms.endswith("/dev"))
        self.assertLessEqual(len(sms.encode("utf-8")), 90)

    def test_sms_compacts_multiple_reasons_and_rejects_untrusted_status(self):
        sms = _build_user_incident_sms(
            {
                "total": 3,
                "rows": [
                    {
                        "tenant_id": self.tenant.id,
                        "tenant_code": self.tenant.code,
                        "source": "backend",
                        "status": 200,
                        "count": 2,
                    },
                    {
                        "tenant_id": self.tenant.id,
                        "tenant_code": self.tenant.code,
                        "source": "report",
                        "count": 1,
                    },
                ],
            }
        )

        self.assertIn("서버5xx(2)+1종", sms)
        self.assertNotIn("서버200", sms)
        self.assertLessEqual(len(sms.encode("utf-8")), 90)

    def test_sms_marks_counts_over_display_bound(self):
        sms = _build_user_incident_sms(
            {
                "total": 15000,
                "rows": [
                    {
                        "tenant_id": self.tenant.id,
                        "tenant_code": self.tenant.code,
                        "source": "backend",
                        "status": 500,
                        "count": 15000,
                    }
                ],
            }
        )

        self.assertIn("오류9999+건", sms)
        self.assertIn("서버500(9999+)", sms)
        self.assertLessEqual(len(sms.encode("utf-8")), 90)

    def test_successful_delivery_suppresses_duplicate_group(self):
        self._backend_incident()
        result = rule_user_incidents()
        OpsAuditLog.objects.create(
            action=SMS_DELIVERY_ACTION,
            summary="sent",
            payload={"fingerprints": result["fingerprints"]},
            result="success",
        )

        self.assertIsNone(rule_user_incidents())

    @override_settings(
        DEV_ALERTS_SMS_ENABLED=True,
        DEV_ALERTS_SMS_RECIPIENT=CONTROLLED_OPS_PHONE,
    )
    @patch(
        "apps.core.management.commands.check_dev_alerts._verify_ops_sms_delivery",
        return_value={"status": "ok", "sent_success": 1},
    )
    @patch(
        "apps.core.management.commands.check_dev_alerts._send_ops_sms",
        return_value={"status": "ok", "group_id": "group-1"},
    )
    def test_command_sends_once_and_persists_delivery_receipt(
        self,
        send_mock,
        verify_mock,
    ):
        self._backend_incident()
        for index in range(8):
            tenant = Tenant.objects.create(
                code=f"receipt-tenant-{index}",
                name=f"Receipt Tenant {index}",
                is_active=True,
            )
            self._backend_incident(tenant=tenant)
        output = StringIO()

        def verify_after_registration(group_id, wait_seconds):
            receipt = OpsAuditLog.objects.get(action=SMS_DELIVERY_ACTION)
            self.assertEqual(receipt.payload["attempt_state"], "registered")
            self.assertEqual(receipt.payload["provider_group_id"], group_id)
            self.assertEqual(wait_seconds, 120)
            return {"status": "ok", "sent_success": 1}

        verify_mock.side_effect = verify_after_registration

        call_command(
            "check_dev_alerts",
            "--rule",
            "user_incidents",
            "--silent",
            stdout=output,
        )
        call_command(
            "check_dev_alerts",
            "--rule",
            "user_incidents",
            "--silent",
            stdout=output,
        )

        send_mock.assert_called_once()
        verify_mock.assert_called_once_with("group-1", 120)
        receipt = OpsAuditLog.objects.get(action=SMS_DELIVERY_ACTION)
        self.assertEqual(receipt.result, "success")
        self.assertEqual(receipt.payload["recipient_last4"], "7466")
        self.assertEqual(receipt.payload["provider_group_id"], "group-1")
        self.assertEqual(len(receipt.payload["fingerprints"]), 9)
        self.assertEqual(receipt.payload["tenant_count"], 9)
        self.assertEqual(receipt.payload["displayed_tenant_count"], 1)
        self.assertEqual(receipt.payload["omitted_tenant_count"], 8)
        self.assertEqual(len(receipt.payload["body_sha256"]), 64)

    @override_settings(
        DEV_ALERTS_SMS_ENABLED=True,
        DEV_ALERTS_SMS_RECIPIENT=CONTROLLED_OPS_PHONE,
    )
    @patch(
        "apps.core.management.commands.check_dev_alerts._verify_ops_sms_delivery"
    )
    @patch(
        "apps.core.management.commands.check_dev_alerts._send_ops_sms",
        return_value={"status": "ok", "group_id": "ambiguous-group"},
    )
    def test_ambiguous_provider_result_is_reconciled_without_resend(
        self,
        send_mock,
        verify_mock,
    ):
        verify_mock.side_effect = [
            {
                "status": "error",
                "reason": "provider_delivery_timeout",
                "sent_total": 0,
                "sent_success": 0,
                "sent_pending": 0,
                "registered_failed": 0,
            },
            {
                "status": "ok",
                "sent_total": 1,
                "sent_success": 1,
                "sent_pending": 0,
                "registered_failed": 0,
            },
        ]
        self._backend_incident()

        with self.assertRaises(CommandError):
            call_command(
                "check_dev_alerts",
                "--rule",
                "user_incidents",
                "--silent",
                stdout=StringIO(),
            )
        call_command(
            "check_dev_alerts",
            "--rule",
            "user_incidents",
            "--silent",
            stdout=StringIO(),
        )

        send_mock.assert_called_once()
        self.assertEqual(verify_mock.call_args_list[0].args, ("ambiguous-group", 120))
        self.assertEqual(verify_mock.call_args_list[1].args, ("ambiguous-group", 0))
        receipt = OpsAuditLog.objects.get(action=SMS_DELIVERY_ACTION)
        self.assertEqual(receipt.result, "success")
        self.assertEqual(receipt.payload["attempt_state"], "delivered")

    @override_settings(
        DEV_ALERTS_SMS_ENABLED=True,
        DEV_ALERTS_SMS_RECIPIENT=CONTROLLED_OPS_PHONE,
    )
    @patch(
        "apps.core.management.commands.check_dev_alerts._verify_ops_sms_delivery",
        return_value={
            "status": "error",
            "reason": "provider_delivery_timeout",
            "sent_total": 0,
            "sent_success": 0,
            "sent_pending": 0,
            "registered_failed": 0,
        },
    )
    @patch(
        "apps.core.management.commands.check_dev_alerts._send_ops_sms",
        return_value={"status": "ok", "group_id": "pending-group"},
    )
    def test_pending_provider_result_holds_fingerprints_without_resend(
        self,
        send_mock,
        _verify_mock,
    ):
        self._backend_incident()

        with self.assertRaises(CommandError):
            call_command(
                "check_dev_alerts",
                "--rule",
                "user_incidents",
                "--silent",
                stdout=StringIO(),
            )
        call_command(
            "check_dev_alerts",
            "--rule",
            "user_incidents",
            "--silent",
            stdout=StringIO(),
        )

        send_mock.assert_called_once()
        receipt = OpsAuditLog.objects.get(action=SMS_DELIVERY_ACTION)
        self.assertEqual(receipt.result, "failed")
        self.assertEqual(receipt.payload["attempt_state"], "ambiguous")

    @override_settings(
        DEV_ALERTS_SMS_ENABLED=True,
        DEV_ALERTS_SMS_RECIPIENT=CONTROLLED_OPS_PHONE,
    )
    @patch(
        "apps.core.management.commands.check_dev_alerts._verify_ops_sms_delivery",
        return_value={
            "status": "error",
            "reason": "provider_delivery_timeout",
            "sent_total": 0,
            "sent_success": 0,
            "sent_pending": 0,
            "registered_failed": 0,
        },
    )
    @patch("apps.core.management.commands.check_dev_alerts._send_ops_sms")
    def test_aged_ambiguous_attempt_is_reconciled_but_never_resent(
        self,
        send_mock,
        verify_mock,
    ):
        self._backend_incident()
        incident_data = rule_user_incidents()
        attempt = OpsAuditLog.objects.create(
            action=SMS_DELIVERY_ACTION,
            summary="ambiguous",
            payload={
                "fingerprints": incident_data["fingerprints"],
                "provider_group_id": "aged-group",
                "attempt_state": "ambiguous",
            },
            result="failed",
        )
        OpsAuditLog.objects.filter(pk=attempt.pk).update(
            created_at=timezone.now() - timedelta(minutes=20)
        )

        call_command(
            "check_dev_alerts",
            "--rule",
            "user_incidents",
            "--silent",
            stdout=StringIO(),
        )

        send_mock.assert_not_called()
        verify_mock.assert_called_once_with("aged-group", 0)
        self.assertIsNone(rule_user_incidents())

    @override_settings(
        DEV_ALERTS_SMS_ENABLED=True,
        DEV_ALERTS_SMS_RECIPIENT=CONTROLLED_OPS_PHONE,
    )
    @patch("apps.core.management.commands.check_dev_alerts._send_ops_sms")
    def test_aged_no_group_attempt_is_held_without_resend(
        self,
        send_mock,
    ):
        self._backend_incident()
        incident_data = rule_user_incidents()
        attempt = OpsAuditLog.objects.create(
            action=SMS_DELIVERY_ACTION,
            summary="created",
            payload={
                "fingerprints": incident_data["fingerprints"],
                "provider_group_id": "",
                "attempt_state": "created",
            },
            result="failed",
        )
        OpsAuditLog.objects.filter(pk=attempt.pk).update(
            created_at=timezone.now() - timedelta(minutes=20)
        )

        call_command(
            "check_dev_alerts",
            "--rule",
            "user_incidents",
            "--silent",
            stdout=StringIO(),
        )

        send_mock.assert_not_called()
        self.assertIsNone(rule_user_incidents())

    def test_aged_definitive_failure_remains_retryable_after_newer_success(self):
        incident = self._backend_incident()
        OpsAuditLog.objects.filter(pk=incident.pk).update(
            created_at=timezone.now() - timedelta(minutes=70)
        )
        aged_data = rule_user_incidents()
        OpsAuditLog.objects.create(
            action=SMS_DELIVERY_ACTION,
            summary="definitive failure",
            payload={
                "fingerprints": aged_data["fingerprints"],
                "provider_group_id": "failed-group",
                "attempt_state": "definitive_failure",
            },
            result="failed",
        )
        OpsAuditLog.objects.create(
            action=SMS_DELIVERY_ACTION,
            summary="newer unrelated success",
            payload={
                "fingerprints": ["unrelated"],
                "attempt_state": "delivered",
            },
            result="success",
        )

        retry_data = rule_user_incidents()

        self.assertIsNotNone(retry_data)
        self.assertEqual(retry_data["fingerprints"], aged_data["fingerprints"])

    @patch(
        "apps.core.management.commands.check_dev_alerts._verify_ops_sms_delivery",
        return_value={
            "status": "error",
            "reason": "provider_delivery_timeout",
            "sent_total": 0,
            "sent_success": 0,
            "sent_pending": 0,
            "registered_failed": 0,
        },
    )
    def test_reconciliation_is_capped_and_rotates_oldest_first(self, verify_mock):
        total = SMS_MAX_RECONCILIATIONS_PER_RUN + 2
        for index in range(total):
            attempt = OpsAuditLog.objects.create(
                action=SMS_DELIVERY_ACTION,
                summary=f"ambiguous {index}",
                payload={
                    "fingerprints": [f"fingerprint-{index}"],
                    "provider_group_id": f"group-{index}",
                    "attempt_state": "ambiguous",
                },
                result="failed",
            )
            OpsAuditLog.objects.filter(pk=attempt.pk).update(
                updated_at=timezone.now() - timedelta(minutes=total - index)
            )
        _reconcile_unresolved_sms_attempts()

        self.assertEqual(
            verify_mock.call_count,
            SMS_MAX_RECONCILIATIONS_PER_RUN,
        )
        self.assertEqual(
            [call.args[0] for call in verify_mock.call_args_list],
            [
                f"group-{index}"
                for index in range(SMS_MAX_RECONCILIATIONS_PER_RUN)
            ],
        )
        verify_mock.reset_mock()

        _reconcile_unresolved_sms_attempts()

        self.assertEqual(
            [call.args[0] for call in verify_mock.call_args_list[:2]],
            [
                f"group-{SMS_MAX_RECONCILIATIONS_PER_RUN}",
                f"group-{SMS_MAX_RECONCILIATIONS_PER_RUN + 1}",
            ],
        )

    @override_settings(
        DEV_ALERTS_SMS_ENABLED=True,
        DEV_ALERTS_SMS_RECIPIENT=CONTROLLED_OPS_PHONE,
    )
    @patch("apps.core.management.commands.check_dev_alerts._send_ops_sms")
    def test_hourly_attempt_cap_defers_without_consuming_fingerprints(
        self,
        send_mock,
    ):
        self._backend_incident()
        for index in range(12):
            OpsAuditLog.objects.create(
                action=SMS_DELIVERY_ACTION,
                summary=f"failed attempt {index}",
                payload={"attempt_state": "registration_failed"},
                result="failed",
            )

        call_command(
            "check_dev_alerts",
            "--rule",
            "user_incidents",
            "--silent",
            stdout=StringIO(),
        )

        send_mock.assert_not_called()
        self.assertTrue(OpsAuditLog.objects.filter(action=SMS_RATE_LIMIT_ACTION).exists())
        self.assertIsNotNone(rule_user_incidents())


class OperatorSmsSafetyTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(
            code="ops-test-tenant",
            name="운영 테스트 학원",
            is_active=True,
        )

    @override_settings(
        DEV_ALERTS_SMS_RECIPIENT="01099999999",
        SOLAPI_SENDER="01011112222",
    )
    def test_sender_rejects_any_non_controlled_recipient_before_provider_call(self):
        with patch(
            "apps.core.management.commands.check_dev_alerts._get_solapi_client"
        ) as client_mock:
            result = _send_ops_sms("[학원+] 테스트")

        self.assertEqual(result["status"], "error")
        self.assertIn("recipient_not_allowed", result["reason"])
        client_mock.assert_not_called()

    @override_settings(
        DEV_ALERTS_SMS_RECIPIENT=CONTROLLED_OPS_PHONE,
        SOLAPI_SENDER="01011112222",
    )
    def test_sender_forces_sms_and_returns_provider_group(self):
        count = SimpleNamespace(registered_success=1, registered_failed=0)
        client = Mock()
        client.send.return_value = SimpleNamespace(
            group_info=SimpleNamespace(group_id="group-safe", count=count)
        )
        with patch(
            "apps.core.management.commands.check_dev_alerts._get_solapi_client",
            return_value=client,
        ):
            result = _send_ops_sms("[학원+] 테스트")

        self.assertEqual(result, {"status": "ok", "group_id": "group-safe"})
        message = client.send.call_args.args[0]
        self.assertEqual(str(message.to), CONTROLLED_OPS_PHONE)
        self.assertEqual(str(message.type), "SMS")

    @override_settings(
        DEV_ALERTS_SMS_ENABLED=True,
        DEV_ALERTS_SMS_RECIPIENT=CONTROLLED_OPS_PHONE,
    )
    @patch(
        "apps.core.management.commands.check_dev_alerts._verify_ops_sms_delivery",
        return_value={"status": "ok", "sent_success": 1},
    )
    @patch(
        "apps.core.management.commands.check_dev_alerts._send_ops_sms",
        return_value={"status": "ok", "group_id": "test-safe"},
    )
    def test_test_sms_contains_tenant_and_controlled_reason(
        self,
        send_mock,
        verify_mock,
    ):
        with override_settings(OWNER_TENANT_ID=self.tenant.id):
            call_command(
                "check_dev_alerts",
                "--test-sms",
                "--wait-seconds",
                "120",
                stdout=StringIO(),
            )

        text = send_mock.call_args.args[0]
        self.assertIn(f"{self.tenant.code}#{self.tenant.id}", text)
        self.assertIn("서버500(1)", text)
        self.assertNotIn("010", text)
        self.assertLessEqual(len(text.encode("utf-8")), 90)
        verify_mock.assert_called_once_with("test-safe", 120)

    @override_settings(
        DEV_ALERTS_SMS_ENABLED=True,
        DEV_ALERTS_SMS_RECIPIENT=CONTROLLED_OPS_PHONE,
    )
    @patch(
        "apps.core.management.commands.check_dev_alerts._verify_ops_sms_delivery",
        return_value={"status": "ok", "sent_success": 1},
    )
    @patch(
        "apps.core.management.commands.check_dev_alerts._send_ops_sms",
        return_value={"status": "ok", "group_id": "external-safe"},
    )
    def test_external_signal_uses_fixed_privacy_safe_sms(
        self,
        send_mock,
        verify_mock,
    ):
        output = StringIO()

        call_command(
            "check_dev_alerts",
            "--external-signal",
            "api_user_impact",
            "--wait-seconds",
            "120",
            stdout=output,
        )

        text = send_mock.call_args.args[0]
        self.assertLessEqual(len(text.encode("utf-8")), 90)
        self.assertNotIn("tenant", text.lower())
        verify_mock.assert_called_once_with("external-safe", 120)
        receipt = OpsAuditLog.objects.get(action="alerts.external_signal_sms")
        self.assertEqual(receipt.result, "success")
