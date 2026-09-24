from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.models import Program, Tenant, TenantMembership
from apps.core.views.auth import MeView


@override_settings(BILLING_GRACE_PERIOD_DAYS=30, BILLING_EXEMPT_TENANT_IDS=set())
class SubscriptionLoginNoticeTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(code="notice-test", name="Notice", is_active=True)
        self.program = Program.objects.get(tenant=self.tenant)
        self.program.subscription_status = "grace"
        self.program.subscription_expires_at = timezone.localdate() - timedelta(days=8)
        self.program.save()
        self.factory = APIRequestFactory()

    def user(self, role, *, tenant=None, is_staff=False):
        tenant = tenant or self.tenant
        user = get_user_model().objects.create_user(
            username=f"notice-{tenant.pk}-{role}", password="test-password",
            tenant=tenant, is_staff=is_staff,
        )
        TenantMembership.objects.create(tenant=tenant, user=user, role=role, is_active=True)
        return user

    def me(self, user, *, tenant=None):
        request = self.factory.get("/api/v1/core/me/")
        request.tenant = tenant or self.tenant
        force_authenticate(request, user=user)
        return MeView.as_view()(request)

    def test_staff_roles_receive_notice_and_can_keep_using_service(self):
        for role in ("owner", "admin", "teacher", "staff"):
            with self.subTest(role=role):
                response = self.me(self.user(role))
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.data["subscription_notice"], {
                    "subscription_expires_at": self.program.subscription_expires_at.isoformat(),
                    "service_access_expires_at": (
                        self.program.subscription_expires_at + timedelta(days=30)
                    ).isoformat(),
                    "days_overdue": 8,
                    "days_remaining": 22,
                })
                self.assertTrue(self.program.is_subscription_active)

    def test_student_and_parent_never_receive_billing_notice_even_with_staff_flag(self):
        for role in ("student", "parent"):
            with self.subTest(role=role):
                response = self.me(self.user(role, is_staff=True))
                self.assertEqual(response.status_code, 200)
                self.assertIsNone(response.data["subscription_notice"])

    def test_membership_in_other_tenant_cannot_read_notice(self):
        other = Tenant.objects.create(code="notice-other", name="Other", is_active=True)
        response = self.me(self.user("teacher", tenant=other))
        self.assertEqual(response.status_code, 403)

    def test_inactive_membership_is_rejected(self):
        user = self.user("staff")
        TenantMembership.objects.filter(user=user, tenant=self.tenant).update(is_active=False)
        self.assertEqual(self.me(user).status_code, 403)

    def test_renewal_clears_notice_on_next_me_without_changing_account(self):
        user = self.user("teacher")
        self.assertIsNotNone(self.me(user).data["subscription_notice"])
        self.program.subscription_status = "active"
        self.program.subscription_expires_at = timezone.localdate() + timedelta(days=30)
        self.program.save()
        self.assertIsNone(self.me(user).data["subscription_notice"])

    def test_no_notice_for_exempt_cancelled_missing_or_expired_subscription(self):
        user = self.user("teacher")
        with override_settings(BILLING_EXEMPT_TENANT_IDS={self.tenant.pk}):
            self.assertIsNone(self.me(user).data["subscription_notice"])
        self.program.cancel_at_period_end = True
        self.program.save()
        self.assertIsNone(self.me(user).data["subscription_notice"])
        self.program.cancel_at_period_end = False
        self.program.subscription_status = "expired"
        self.program.save()
        self.assertIsNone(self.me(user).data["subscription_notice"])
        self.program.delete()
        self.assertIsNone(self.me(user).data["subscription_notice"])
