from __future__ import annotations

from unittest.mock import patch
from uuid import uuid4

import pytest
from django.apps import apps
from django.contrib.auth import get_user_model
from rest_framework.test import APIRequestFactory

from apps.domains.matchup.models import MatchupDocument, MatchupProblem
from apps.domains.matchup.views import ProblemDetailView


@pytest.mark.django_db
def test_single_problem_delete_allows_manual_problem():
    Tenant = apps.get_model("core", "Tenant")
    TenantMembership = apps.get_model("core", "TenantMembership")
    InventoryFile = apps.get_model("inventory", "InventoryFile")

    suffix = uuid4().hex[:8]
    tenant = Tenant.objects.create(code=f"manual-delete-{suffix}", name="manual-delete")
    inventory = InventoryFile.objects.create(
        tenant=tenant,
        scope="admin",
        student_ps="",
        display_name="manual-delete.pdf",
        r2_key=f"manual-delete-{suffix}.pdf",
        original_name="manual-delete.pdf",
        content_type="application/pdf",
        size_bytes=0,
    )
    document = MatchupDocument.objects.create(
        tenant=tenant,
        inventory_file=inventory,
        title="manual-delete-doc",
        r2_key=inventory.r2_key,
        original_name=inventory.original_name,
        content_type=inventory.content_type,
        size_bytes=inventory.size_bytes,
    )
    manual = MatchupProblem.objects.create(
        tenant=tenant,
        document=document,
        number=987,
        text="manual",
        meta={"manual": True},
    )
    user = get_user_model().objects.create_user(
        username=f"manual-delete-{suffix}",
        password="test1234",
        tenant=tenant,
    )
    TenantMembership.ensure_active(tenant=tenant, user=user, role="teacher")

    request = APIRequestFactory().delete(f"/api/v1/matchup/problems/{manual.id}/")
    request.tenant = tenant

    with (
        patch("apps.domains.matchup.views.JWTAuthentication.authenticate", return_value=(user, None)),
        patch("apps.domains.matchup.views.delete_problem_with_r2", side_effect=lambda problem: problem.delete()) as deleter,
    ):
        response = ProblemDetailView.as_view()(request, problem_id=manual.id)

    assert response.status_code == 200
    deleter.assert_called_once()
    assert not MatchupProblem.objects.filter(id=manual.id).exists()
