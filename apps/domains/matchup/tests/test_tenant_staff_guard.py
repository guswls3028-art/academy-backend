from __future__ import annotations

from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.models import Tenant, TenantMembership
from apps.domains.matchup.views import _hit_report_writable, _is_tenant_admin, _is_tenant_staff


User = get_user_model()


class MatchupTenantStaffGuardTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.tenant_a = Tenant.objects.create(code="matchup-a", name="matchup-a", is_active=True)
        self.tenant_b = Tenant.objects.create(code="matchup-b", name="matchup-b", is_active=True)
        self.staff = User.objects.create_user(
            username="matchup-global-staff",
            password="test1234",
            tenant=self.tenant_a,
            is_staff=True,
        )

    def _request(self, tenant):
        request = self.factory.get("/api/v1/matchup/documents/")
        force_authenticate(request, user=self.staff)
        request.user = self.staff
        request.tenant = tenant
        return request

    def test_django_staff_flag_does_not_grant_other_tenant_matchup_staff(self):
        self.assertFalse(_is_tenant_staff(self._request(self.tenant_b)))
        self.assertFalse(_is_tenant_admin(self._request(self.tenant_b)))

    def test_django_staff_flag_allows_only_own_tenant(self):
        self.assertTrue(_is_tenant_staff(self._request(self.tenant_a)))
        self.assertTrue(_is_tenant_admin(self._request(self.tenant_a)))

    def test_membership_allows_matchup_staff_and_admin_by_role(self):
        TenantMembership.ensure_active(tenant=self.tenant_b, user=self.staff, role="teacher")
        self.assertTrue(_is_tenant_staff(self._request(self.tenant_b)))
        self.assertFalse(_is_tenant_admin(self._request(self.tenant_b)))

        TenantMembership.objects.filter(tenant=self.tenant_b, user=self.staff).update(role="owner")
        self.assertTrue(_is_tenant_admin(self._request(self.tenant_b)))

    def test_hit_report_writable_does_not_allow_global_staff_cross_tenant(self):
        report = SimpleNamespace(author_id=None)

        self.assertFalse(_hit_report_writable(self._request(self.tenant_b), report))
