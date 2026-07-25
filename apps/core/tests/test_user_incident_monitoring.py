from __future__ import annotations

from io import StringIO
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.core.management import call_command
from django.http import HttpRequest, JsonResponse
from django.test import TestCase, TransactionTestCase, override_settings
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.api.common.middleware import UnhandledExceptionMiddleware
from apps.api.common import middleware as incident_middleware
from apps.api.common.throttles import UserIncidentReportThrottle
from apps.core.management.commands.check_dev_alerts import (
    CONTROLLED_OPS_PHONE,
    SMS_DELIVERY_ACTION,
    _build_user_incident_sms,
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

    def _backend_incident(self):
        return OpsAuditLog.objects.create(
            action="user_incident.backend_5xx",
            summary="GET route returned 500",
            target_tenant=self.tenant,
            payload={
                "source": "backend_5xx",
                "route": "api/v1/results/<int:pk>/",
                "method": "GET",
                "status": 500,
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
        self.assertNotIn(self.tenant.name, sms)

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
        output = StringIO()

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


class OperatorSmsSafetyTests(TestCase):
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
