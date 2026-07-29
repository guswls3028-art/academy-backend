from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.models import Tenant, TenantMembership
from apps.core.serializers import UserSerializer
from apps.core.views.auth import CompleteFirstLoginGuideView
from apps.domains.parents.models import Parent


User = get_user_model()


class FirstLoginGuideTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.tenant = Tenant.objects.create(
            code="first-login-guide",
            name="First Login Guide",
            is_active=True,
        )

    def _create_user(self, role: str, *, completed: bool = False):
        user = User.objects.create_user(
            username=f"first-login-{role}",
            password="pw1234",
            tenant=self.tenant,
            first_login_guide_completed_at=timezone.now() if completed else None,
        )
        TenantMembership.ensure_active(
            tenant=self.tenant,
            user=user,
            role=role,
        )
        if role == "parent":
            Parent.objects.create(
                tenant=self.tenant,
                user=user,
                name="First Login Parent",
                phone=f"0100000{user.id:04d}",
            )
        return user

    def _request(self, view, user, method: str, path: str):
        request = getattr(self.factory, method)(path)
        request.tenant = self.tenant
        force_authenticate(request, user=user)
        return view.as_view()(request)

    def test_me_requires_guide_for_each_tenant_role_until_completed(self):
        for role in ("owner", "admin", "teacher", "staff", "student", "parent"):
            with self.subTest(role=role):
                user = self._create_user(role)
                request = self.factory.get("/api/v1/core/me/")
                request.tenant = self.tenant
                data = UserSerializer(
                    user,
                    context={"request": request},
                )

                self.assertTrue(data.data["first_login_guide_required"])

    def test_me_does_not_require_guide_after_completion(self):
        user = self._create_user("teacher", completed=True)
        request = self.factory.get("/api/v1/core/me/")
        request.tenant = self.tenant

        data = UserSerializer(
            user,
            context={"request": request},
        )

        self.assertFalse(data.data["first_login_guide_required"])

    def test_completion_is_account_scoped_and_idempotent(self):
        user = self._create_user("parent")
        path = "/api/v1/core/me/first-login-guide/complete/"

        first = self._request(
            CompleteFirstLoginGuideView,
            user,
            "post",
            path,
        )
        user.refresh_from_db()
        first_completed_at = user.first_login_guide_completed_at

        second = self._request(
            CompleteFirstLoginGuideView,
            user,
            "post",
            path,
        )
        user.refresh_from_db()

        self.assertEqual(first.status_code, 200, first.data)
        self.assertEqual(second.status_code, 200, second.data)
        self.assertFalse(first.data["first_login_guide_required"])
        self.assertEqual(user.first_login_guide_completed_at, first_completed_at)

    def test_completion_requires_active_membership_in_current_tenant(self):
        user = User.objects.create_user(
            username="first-login-no-membership",
            password="pw1234",
            tenant=self.tenant,
        )

        response = self._request(
            CompleteFirstLoginGuideView,
            user,
            "post",
            "/api/v1/core/me/first-login-guide/complete/",
        )
        user.refresh_from_db()

        self.assertEqual(response.status_code, 403)
        self.assertIsNone(user.first_login_guide_completed_at)
