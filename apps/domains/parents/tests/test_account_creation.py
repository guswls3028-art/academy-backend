from django.test import TestCase

from apps.core.models import Tenant, TenantMembership
from apps.domains.parents.models import Parent
from apps.domains.parents.services import (
    ensure_parent_account_for_student,
    parent_initial_password,
)


class ParentAccountCreationTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Parent Account", code="parent-account")

    def test_phone_is_normalized_before_identity_creation(self):
        result = ensure_parent_account_for_student(
            tenant=self.tenant,
            parent_phone="010-1234-5678",
            student_name="학생",
        )

        self.assertEqual(result.parent.phone, "01012345678")
        self.assertEqual(result.initial_password, "5678")
        self.assertEqual(result.parent.user.username, f"p_{self.tenant.id}_01012345678")

    def test_invalid_phone_cannot_fall_back_to_shared_password(self):
        with self.assertRaisesMessage(ValueError, "010 11자리"):
            parent_initial_password("")

        self.assertFalse(Parent.objects.filter(tenant=self.tenant).exists())

    def test_repeated_ensure_is_idempotent(self):
        first = ensure_parent_account_for_student(
            tenant=self.tenant,
            parent_phone="01012345678",
            student_name="첫째",
        )
        second = ensure_parent_account_for_student(
            tenant=self.tenant,
            parent_phone="01012345678",
            student_name="둘째",
        )

        self.assertEqual(first.parent.id, second.parent.id)
        self.assertTrue(first.user_created)
        self.assertFalse(second.user_created)
        self.assertEqual(Parent.objects.filter(tenant=self.tenant).count(), 1)
        self.assertEqual(
            TenantMembership.objects.filter(
                tenant=self.tenant,
                user=first.parent.user,
                role="parent",
                is_active=True,
            ).count(),
            1,
        )
