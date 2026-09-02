from __future__ import annotations

import threading
import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.db import close_old_connections, connection
from django.test import TransactionTestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.models import Tenant, TenantMembership
from apps.domains.landing_public.api.views.matchup_showcase_views import (
    PublicMatchupShowcaseViewSet,
)
from apps.domains.landing_public.models import PublicMatchupShowcase


User = get_user_model()
pytestmark = pytest.mark.django_db(transaction=True)


class MatchupShowcasePublishConcurrencyPostgresTests(TransactionTestCase):
    @classmethod
    def setUpClass(cls):
        if connection.vendor != "postgresql":
            raise unittest.SkipTest(
                "PostgreSQL is required for matchup showcase advisory-lock verification."
            )
        super().setUpClass()

    def test_concurrent_publish_builds_one_snapshot_and_returns_one_row(self):
        suffix = uuid.uuid4().hex[:8]
        tenant = Tenant.objects.create(
            name=f"Showcase race {suffix}",
            code=f"showcase-race-{suffix}",
        )
        owner = User.objects.create_user(
            username=f"showcase-race-owner-{suffix}",
            password="pw1234",
            tenant=tenant,
            is_staff=True,
        )
        TenantMembership.ensure_active(tenant=tenant, user=owner, role="owner")
        barrier = threading.Barrier(2, timeout=10)
        responses: list[tuple[int, int, str | None]] = []
        errors: list[Exception] = []
        build_calls = 0
        build_calls_lock = threading.Lock()

        def build_snapshot(_tenant, _hit_report_id):
            nonlocal build_calls
            with build_calls_lock:
                build_calls += 1
            return (
                f"matchup-showcase-snapshots/tenant_{tenant.id}/generated.pdf",
                123,
                {"source": "generated"},
            )

        def worker():
            close_old_connections()
            try:
                thread_tenant = Tenant.objects.get(pk=tenant.pk)
                thread_owner = User.objects.get(pk=owner.pk)
                request = APIRequestFactory().post(
                    "/api/v1/landing-public/matchup-showcase/publish/",
                    {"hit_report_id": 42},
                    format="json",
                )
                request.tenant = thread_tenant
                force_authenticate(request, user=thread_owner)
                barrier.wait()
                response = PublicMatchupShowcaseViewSet.as_view({"post": "publish"})(request)
                responses.append(
                    (
                        response.status_code,
                        response.data["id"],
                        response.headers.get("X-Idempotent-Replay"),
                    )
                )
            except Exception as exc:  # pragma: no cover - surfaced below
                errors.append(exc)
            finally:
                close_old_connections()

        report = SimpleNamespace(id=42, title="Concurrent report", document_id=None)
        with (
            patch(
                "apps.domains.landing_public.api.views.matchup_showcase_views."
                "get_matchup_hit_report_for_showcase",
                return_value=report,
            ),
            patch(
                "apps.domains.landing_public.api.views.matchup_showcase_views."
                "build_matchup_snapshot_for_hit_report",
                side_effect=build_snapshot,
            ),
        ):
            threads = [threading.Thread(target=worker) for _ in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=20)

        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(errors, [])
        self.assertEqual(build_calls, 1)
        self.assertCountEqual([row[0] for row in responses], [201, 200])
        self.assertCountEqual([row[2] for row in responses], [None, "true"])
        self.assertEqual(len({row[1] for row in responses}), 1)
        self.assertEqual(
            PublicMatchupShowcase.objects.filter(
                tenant=tenant,
                hit_report_id_ref=42,
            ).count(),
            1,
        )
