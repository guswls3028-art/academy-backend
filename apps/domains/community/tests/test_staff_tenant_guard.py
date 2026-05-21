from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.models import Tenant, TenantMembership
from apps.domains.community.api.views.post_views import PostViewSet


User = get_user_model()


class CommunityStaffTenantGuardTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.tenant_a = Tenant.objects.create(code="comm-staff-a", name="comm-staff-a", is_active=True)
        self.tenant_b = Tenant.objects.create(code="comm-staff-b", name="comm-staff-b", is_active=True)
        self.staff = User.objects.create_user(
            username="community-global-staff",
            password="test1234",
            tenant=self.tenant_a,
            is_staff=True,
        )
        self.view = PostViewSet()

    def _request(self, tenant):
        request = self.factory.get("/api/v1/community/posts/")
        force_authenticate(request, user=self.staff)
        request.user = self.staff
        request.tenant = tenant
        return request

    def test_django_staff_flag_does_not_grant_other_tenant_community_staff(self):
        request = self._request(self.tenant_b)

        self.assertFalse(self.view._is_staff_request(request))
        self.assertFalse(self.view._can_manage_post_nodes(request))

    def test_django_staff_flag_allows_only_own_tenant(self):
        request = self._request(self.tenant_a)

        self.assertTrue(self.view._is_staff_request(request))
        self.assertTrue(self.view._can_manage_post_nodes(request))

    def test_membership_allows_community_staff_by_role(self):
        TenantMembership.ensure_active(tenant=self.tenant_b, user=self.staff, role="teacher")

        request = self._request(self.tenant_b)

        self.assertTrue(self.view._is_staff_request(request))
        self.assertFalse(self.view._can_manage_post_nodes(request))

        TenantMembership.objects.filter(tenant=self.tenant_b, user=self.staff).update(role="admin")
        self.assertTrue(self.view._can_manage_post_nodes(request))
