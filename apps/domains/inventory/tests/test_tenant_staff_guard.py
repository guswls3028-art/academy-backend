from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.models import Tenant, TenantMembership
from apps.domains.inventory.views import _is_tenant_staff


User = get_user_model()


class InventoryTenantStaffGuardTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.tenant_a = Tenant.objects.create(code="inv-a", name="inv-a", is_active=True)
        self.tenant_b = Tenant.objects.create(code="inv-b", name="inv-b", is_active=True)
        self.staff = User.objects.create_user(
            username="global-staff",
            password="test1234",
            tenant=self.tenant_a,
            is_staff=True,
        )

    def _request(self, tenant):
        req = self.factory.get("/storage/files/?scope=admin")
        force_authenticate(req, user=self.staff)
        req.user = self.staff
        req.tenant = tenant
        return req

    def test_global_staff_without_membership_cannot_access_other_tenant(self):
        self.assertFalse(_is_tenant_staff(self._request(self.tenant_b)))

    def test_global_staff_can_access_own_tenant(self):
        self.assertTrue(_is_tenant_staff(self._request(self.tenant_a)))

    def test_membership_allows_tenant_staff(self):
        TenantMembership.ensure_active(tenant=self.tenant_b, user=self.staff, role="teacher")
        self.assertTrue(_is_tenant_staff(self._request(self.tenant_b)))

    def test_staff_role_allows_tenant_staff(self):
        TenantMembership.ensure_active(tenant=self.tenant_b, user=self.staff, role="staff")
        self.assertTrue(_is_tenant_staff(self._request(self.tenant_b)))
