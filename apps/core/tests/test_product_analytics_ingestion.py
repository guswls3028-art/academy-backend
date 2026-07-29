from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.models import ProductUsageEvent, Program, Tenant, TenantMembership
from apps.core.product_analytics.views import ProductUsageBatchView


class ProductUsageIngestionTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.tenant = Tenant.objects.create(
            code="analytics-tenant",
            name="Analytics Tenant",
            is_active=True,
        )
        self.program, _ = Program.objects.get_or_create(tenant=self.tenant)
        self.program.feature_flags = {"product_usage_analytics_enabled": True}
        self.program.save(update_fields=["feature_flags"])
        self.user = get_user_model().objects.create_user(
            username="analytics-teacher",
            password="test1234",
            tenant=self.tenant,
        )
        TenantMembership.objects.create(
            tenant=self.tenant,
            user=self.user,
            role="teacher",
            is_active=True,
        )

    def event(self, **overrides):
        event = {
            "event_id": str(uuid4()),
            "event_type": "screen_view",
            "occurred_at": timezone.now().isoformat(),
            "session_id": str(uuid4()),
            "view_id": str(uuid4()),
            "feature_id": "attendance.mark",
            "screen_id": "teacher.attendance.home",
            "surface": "teacher",
            "route_template": "/teacher/attendance",
            "device_class": "desktop",
            "client_release": "test-release",
            "catalog_version": "2026-07-29",
        }
        event.update(overrides)
        return event

    def request(self, payload, *, user=None, tenant=None, auth=None, **extra):
        request = self.factory.post(
            "/api/v1/core/product-analytics/events/batch/",
            payload,
            format="json",
            **extra,
        )
        request.tenant = self.tenant if tenant is None else tenant
        if user is not False:
            force_authenticate(
                request,
                user=user or self.user,
                token=auth,
            )
        return ProductUsageBatchView.as_view()(request)

    def test_server_derives_tenant_role_and_actor_hash(self):
        response = self.request(
            {"schema_version": 1, "events": [self.event()]}
        )

        self.assertEqual(response.status_code, 202, response.data)
        self.assertEqual(response.data, {"accepted": 1, "duplicates": 0})
        saved = ProductUsageEvent.objects.get()
        self.assertEqual(saved.tenant, self.tenant)
        self.assertEqual(saved.role, "teacher")
        self.assertEqual(saved.audience_group, "teacher_staff")
        self.assertEqual(len(saved.actor_hash), 64)
        self.assertNotEqual(saved.actor_hash, str(self.user.id))

    def test_feature_flag_off_ignores_without_persistence(self):
        self.program.feature_flags = {}
        self.program.save(update_fields=["feature_flags"])

        response = self.request(
            {"schema_version": 1, "events": [self.event()]}
        )

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.data["ignored"], "feature_disabled")
        self.assertEqual(ProductUsageEvent.objects.count(), 0)

    def test_missing_membership_is_forbidden(self):
        outsider = get_user_model().objects.create_user(
            username="analytics-outsider",
            password="test1234",
        )

        response = self.request(
            {"schema_version": 1, "events": [self.event()]},
            user=outsider,
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(ProductUsageEvent.objects.count(), 0)

    def test_client_identity_and_freeform_fields_are_rejected(self):
        event = self.event(
            tenant_id=self.tenant.id,
            role="owner",
            user_id=self.user.id,
            properties={"student_name": "do-not-store"},
        )

        response = self.request({"schema_version": 1, "events": [event]})

        self.assertEqual(response.status_code, 400)
        self.assertEqual(ProductUsageEvent.objects.count(), 0)
        for field in ("tenant_id", "role", "user_id", "properties"):
            self.assertIn(field, response.data["events"][0])

    def test_duplicate_event_id_is_not_stored_twice(self):
        event = self.event()
        first = self.request({"schema_version": 1, "events": [event]})
        second = self.request({"schema_version": 1, "events": [event]})

        self.assertEqual(first.data["accepted"], 1)
        self.assertEqual(second.data, {"accepted": 0, "duplicates": 1})
        self.assertEqual(ProductUsageEvent.objects.count(), 1)

    def test_invalid_time_dynamic_route_and_batch_size_are_rejected(self):
        old = self.event(
            occurred_at=(timezone.now() - timedelta(hours=25)).isoformat(),
        )
        old_response = self.request(
            {"schema_version": 1, "events": [old]}
        )
        dynamic_response = self.request(
            {
                "schema_version": 1,
                "events": [
                    self.event(
                        route_template=f"/teacher/students/{uuid4()}?tab=score"
                    )
                ],
            }
        )
        large_response = self.request(
            {
                "schema_version": 1,
                "events": [self.event() for _ in range(21)],
            }
        )

        self.assertEqual(old_response.status_code, 400)
        self.assertEqual(dynamic_response.status_code, 400)
        self.assertEqual(large_response.status_code, 400)
        self.assertEqual(ProductUsageEvent.objects.count(), 0)

    def test_single_digit_dynamic_route_id_is_rejected(self):
        response = self.request(
            {
                "schema_version": 1,
                "events": [
                    self.event(route_template="/student/exams/1")
                ],
            }
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(ProductUsageEvent.objects.count(), 0)

    def test_cta_and_failure_contract_is_enforced(self):
        cta_response = self.request(
            {
                "schema_version": 1,
                "events": [self.event(event_type="cta_click")],
            }
        )
        failure_response = self.request(
            {
                "schema_version": 1,
                "events": [
                    self.event(
                        event_type="task_failure",
                        interaction_id=str(uuid4()),
                        action_id="attendance.save",
                    )
                ],
            }
        )

        self.assertEqual(cta_response.status_code, 400)
        self.assertEqual(failure_response.status_code, 400)

    def test_impersonated_claim_is_recorded(self):
        response = self.request(
            {"schema_version": 1, "events": [self.event()]},
            auth={"impersonated_by": 999},
        )

        self.assertEqual(response.status_code, 202)
        self.assertTrue(ProductUsageEvent.objects.get().is_impersonated)

    def test_declared_payload_over_limit_is_rejected(self):
        response = self.request(
            {"schema_version": 1, "events": [self.event()]},
            CONTENT_LENGTH=str(64 * 1024 + 1),
        )

        self.assertEqual(response.status_code, 413)
        self.assertEqual(ProductUsageEvent.objects.count(), 0)
