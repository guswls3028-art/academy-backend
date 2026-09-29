from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.models import (
    OpsAuditLog,
    ProductUsageDailyActor,
    Tenant,
    TenantDomain,
    TenantMembership,
)
from apps.core.product_analytics.views import ProductUsageOverviewView


class ProductUsageOverviewTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.platform = Tenant.objects.create(
            code="analytics-platform",
            name="Analytics Platform",
            is_active=True,
        )
        self.target = Tenant.objects.create(
            code="analytics-target",
            name="Analytics Target",
            is_active=True,
        )
        self.user = get_user_model().objects.create_superuser(
            username="analytics-platform-owner",
            password="test1234",
            tenant=self.platform,
        )
        TenantMembership.objects.create(
            tenant=self.platform,
            user=self.user,
            role="owner",
            is_active=True,
        )

    def daily(self, *, actor: str, event_type: str, count: int):
        now = timezone.now()
        ProductUsageDailyActor.objects.create(
            day=timezone.localdate(),
            tenant=self.target,
            actor_hash=actor,
            role="teacher",
            audience_group="teacher_staff",
            surface="teacher",
            feature_id="attendance.mark",
            screen_id="teacher.attendance.home",
            event_type=event_type,
            cta_id="attendance.save" if event_type.startswith("cta_") else "",
            action_id=(
                "attendance.save" if event_type.startswith("task_") else ""
            ),
            placement_id="teacher.page.primary",
            position_index=0,
            device_class="desktop",
            client_release="test-release",
            catalog_version="2026-07-29",
            synthetic=False,
            is_impersonated=False,
            count=count,
            first_at=now - timedelta(minutes=1),
            last_at=now,
        )

    def request(self, filters: dict):
        request = self.factory.post(
            "/api/v1/core/dev/product-analytics/overview/",
            filters,
            format="json",
        )
        request.tenant = self.platform
        force_authenticate(request, user=self.user)
        with override_settings(OWNER_TENANT_ID=self.platform.id):
            return ProductUsageOverviewView.as_view()(request)

    def test_platform_admin_gets_role_feature_and_completion_metrics(self):
        for index in range(5):
            actor = f"{index:064d}"
            self.daily(actor=actor, event_type="screen_view", count=2)
            self.daily(actor=actor, event_type="screen_engaged", count=1)
            self.daily(actor=actor, event_type="task_start", count=1)
            self.daily(actor=actor, event_type="task_success", count=1)

        response = self.request({
            "days": 28, "tenant_id": self.target.id,
            "role": "teacher", "surface": "teacher",
        })

        self.assertEqual(response.status_code, 200, response.data)
        self.assertFalse(response.data["suppressed"])
        self.assertEqual(response.data["summary"]["active_actors"], 5)
        self.assertEqual(response.data["summary"]["engagement_rate"], 0.5)
        self.assertEqual(response.data["summary"]["task_completion_rate"], 1.0)
        self.assertEqual(response.data["features"][0]["feature_id"], "attendance.mark")

    def test_small_single_tenant_cell_is_suppressed(self):
        self.daily(actor="a" * 64, event_type="screen_view", count=1)

        response = self.request({"days": 28, "tenant_id": self.target.id})

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["suppressed"])
        self.assertIsNone(response.data["summary"]["active_actors"])
        self.assertEqual(response.data["features"], [])

    def test_invalid_numeric_filters_never_query_or_audit(self):
        with (
            patch("apps.core.product_analytics.views.build_overview") as overview,
            patch("apps.core.product_analytics.views.record_audit") as audit,
        ):
            invalid_filters = [
                {"days": value} for value in (True, 7.9, "7", None)
            ] + [
                {"days": 28, "tenant_id": value}
                for value in (True, 1.9, 0, -1, 2**63, "1", "")
            ]
            for filters in invalid_filters:
                with self.subTest(filters=filters):
                    response = self.request(filters)
                    self.assertEqual(response.status_code, 400, response.data)
            overview.assert_not_called()
            audit.assert_not_called()

    def test_non_platform_tenant_is_forbidden(self):
        outsider = get_user_model().objects.create_user(
            username="analytics-non-platform",
            password="test1234",
            tenant=self.target,
        )
        TenantMembership.objects.create(
            tenant=self.target,
            user=outsider,
            role="owner",
            is_active=True,
        )
        request = self.factory.post(
            "/api/v1/core/dev/product-analytics/overview/",
            {"days": 28},
            format="json",
        )
        request.tenant = self.target
        force_authenticate(request, user=outsider)

        with override_settings(OWNER_TENANT_ID=self.platform.id):
            response = ProductUsageOverviewView.as_view()(request)

        self.assertEqual(response.status_code, 403)

    def test_client_boundary_keeps_get_read_only_and_audits_one_post(self):
        TenantDomain.objects.update_or_create(
            tenant=self.platform,
            defaults={"host": "testserver", "is_primary": True, "is_active": True},
        )
        TenantDomain.objects.update_or_create(
            tenant=self.target,
            defaults={"host": "analytics-target.test", "is_primary": True, "is_active": True},
        )
        self.client.force_login(self.user)
        path = "/api/v1/core/dev/product-analytics/overview/"

        with override_settings(OWNER_TENANT_ID=self.platform.id):
            old_client = self.client.get(path)
            self.assertEqual(old_client.status_code, 405)
            self.assertEqual(old_client.json()["code"], "post_required")
            self.assertFalse(OpsAuditLog.objects.filter(action="product_analytics.view").exists())

            response = self.client.post(
                path, data='{"days":28,"tenant_id":%d}' % self.target.id,
                content_type="application/json",
            )
            self.assertEqual(response.status_code, 200, response.content)
            self.assertEqual(response.json()["filters"]["tenant_id"], self.target.id)
            audits = OpsAuditLog.objects.filter(action="product_analytics.view")
            self.assertEqual(audits.count(), 1)
            self.assertEqual(audits.get().payload["tenant_id"], self.target.id)

            invalid = self.client.post(
                path, data='{"days":"often"}', content_type="application/json",
            )
            self.assertEqual(invalid.status_code, 400)
            invalid_role = self.client.post(
                path, data='{"days":28,"role":0}', content_type="application/json",
            )
            self.assertEqual(invalid_role.status_code, 400)
            cross_tenant = self.client.post(
                path, data='{"days":28}', content_type="application/json",
                HTTP_HOST="analytics-target.test",
            )
            self.assertIn(cross_tenant.status_code, (401, 403))
            self.assertEqual(audits.count(), 1)
