"""Real middleware/JWT regression for read-only identity and explicit setup."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest import skipUnless
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import IntegrityError, connection, connections
from django.test import TestCase, TransactionTestCase
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken

from apps.core.models import Tenant, TenantDomain, TenantMembership
from apps.domains.staffs.models import Staff, StaffWorkType, WorkType


class StaffMeIdentityTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(code="staff-me-owner", name="Owner identity")
        TenantDomain.objects.create(tenant=self.tenant, host="staff-me-owner.test", is_primary=False)
        self.owner = get_user_model().objects.create_user(
            username="staff-me-owner", password="test-only", tenant=self.tenant,
            name="Owner", phone="", is_staff=True,
        )
        self.membership = TenantMembership.objects.create(
            tenant=self.tenant, user=self.owner, role="owner", is_active=True,
        )
        self.client = self.client_for(self.owner)

    def client_for(self, user, host="staff-me-owner.test"):
        token = AccessToken.for_user(user)
        token["tenant_id"] = self.tenant.pk
        token["token_version"] = user.token_version
        client = APIClient(HTTP_HOST=host)
        client.credentials(HTTP_AUTHORIZATION="Bearer " + str(token))
        return client

    def counts(self):
        return Staff.objects.count(), WorkType.objects.count(), StaffWorkType.objects.count()

    def get_me(self):
        response = self.client.get("/api/v1/staffs/me/")
        self.assertEqual(response.status_code, 200, response.data)
        return response.data

    def ensure(self, status=200, data=None):
        response = self.client.post("/api/v1/staffs/me/", data or {}, format="json")
        self.assertEqual(response.status_code, status, response.data)
        return response

    def test_owner_missing_identity_get_post_get_and_repeat(self):
        first = self.get_me()
        self.assertTrue(first["is_owner"])
        self.assertTrue(first["owner_setup_required"])
        self.assertEqual(self.counts(), (0, 0, 0))
        prepared = self.ensure().data
        self.assertFalse(prepared["owner_setup_required"])
        self.assertTrue(prepared["is_owner"])
        self.assertEqual(self.counts(), (1, 1, 1))
        self.assertEqual(self.get_me(), prepared)
        self.assertEqual(self.ensure().data, prepared)
        self.assertEqual(self.counts(), (1, 1, 1))
        self.assertEqual(Staff.objects.get().user_id, self.owner.pk)
        self.assertEqual(self.client.head("/api/v1/staffs/me/").status_code, 200)

    def test_unique_unlinked_manual_staff_missing_type_is_preserved(self):
        staff = Staff.objects.create(tenant=self.tenant, name="Owner", phone="", is_manager=False)
        self.assertTrue(self.get_me()["owner_setup_required"])
        self.assertEqual(self.counts(), (1, 0, 0))
        self.ensure()
        self.assertEqual(self.counts(), (1, 1, 1))
        staff.refresh_from_db()
        self.assertIsNone(staff.user_id)
        self.assertFalse(staff.is_manager)
        self.assertFalse(self.get_me()["owner_setup_required"])
        self.ensure()
        self.assertEqual(self.counts(), (1, 1, 1))

    def test_missing_assignment_uses_existing_type_without_changing_wage(self):
        staff = Staff.objects.create(tenant=self.tenant, name="Owner", phone="")
        work_type = WorkType.objects.create(tenant=self.tenant, name="Manual rate", base_hourly_wage=23123)
        self.assertTrue(self.get_me()["owner_setup_required"])
        self.ensure()
        self.assertEqual(self.counts(), (1, 1, 1))
        self.assertEqual(StaffWorkType.objects.get(staff=staff).effective_hourly_wage, 23123)
        work_type.refresh_from_db()
        self.assertEqual(work_type.name, "Manual rate")
        self.assertEqual(work_type.base_hourly_wage, 23123)

    def test_ready_manual_assignment_and_override_are_unchanged(self):
        staff = Staff.objects.create(tenant=self.tenant, name="Owner", phone="")
        work_type = WorkType.objects.create(tenant=self.tenant, name="Manual type", base_hourly_wage=18000)
        assignment = StaffWorkType.objects.create(
            tenant=self.tenant, staff=staff, work_type=work_type, hourly_wage=25000,
        )
        self.assertFalse(self.get_me()["owner_setup_required"])
        before = self.get_me()
        self.assertEqual(self.ensure().data, before)
        assignment.refresh_from_db()
        self.assertEqual(assignment.hourly_wage, 25000)
        self.assertEqual(self.counts(), (1, 1, 1))

    def test_linked_owner_manual_unassigned_is_not_provisioned(self):
        staff = Staff.objects.create(tenant=self.tenant, user=self.owner, name="Manual owner", phone="")
        before = self.get_me()
        self.assertFalse(before["owner_setup_required"])
        self.assertEqual(before["assigned_work_types"], [])
        self.assertEqual(self.ensure().data, before)
        self.assertEqual(self.counts(), (1, 0, 0))
        self.assertEqual(before["staff_id"], staff.pk)

    def test_foreign_user_one_to_one_is_not_stolen(self):
        other = Tenant.objects.create(code="staff-me-foreign", name="Foreign")
        foreign = Staff.objects.create(tenant=other, user=self.owner, name="Foreign manual", phone="")
        self.assertTrue(self.get_me()["owner_setup_required"])
        prepared = self.ensure().data
        current = Staff.objects.get(tenant=self.tenant)
        self.assertIsNone(current.user_id)
        foreign.refresh_from_db()
        self.assertEqual(foreign.user_id, self.owner.pk)
        self.assertEqual(foreign.name, "Foreign manual")
        self.assertEqual(self.get_me(), prepared)
        self.ensure()
        self.assertEqual(self.counts(), (2, 1, 1))

    def test_ambiguous_or_other_account_match_fails_without_adoption(self):
        for _ in range(2):
            Staff.objects.create(tenant=self.tenant, name="Owner", phone="")
        self.assertTrue(self.get_me()["is_owner"])
        self.assertTrue(self.get_me()["owner_setup_required"])
        self.ensure(status=409)
        self.assertEqual(self.counts(), (2, 0, 0))
        Staff.objects.all().delete()
        other_user = get_user_model().objects.create_user(username="another-staff", tenant=self.tenant)
        manual = Staff.objects.create(tenant=self.tenant, user=other_user, name="Owner", phone="")
        self.ensure(status=409)
        manual.refresh_from_db()
        self.assertEqual(manual.user_id, other_user.pk)
        self.assertEqual(self.counts(), (1, 0, 0))

    def test_teacher_admin_staff_and_revoked_owner_cannot_setup(self):
        for role in ("teacher", "admin", "staff"):
            with self.subTest(role=role):
                self.membership.role = role
                self.membership.save(update_fields=["role"])
                before = self.get_me()
                self.assertFalse(before["owner_setup_required"])
                self.ensure(status=403)
                self.assertEqual(self.counts(), (0, 0, 0))
        self.membership.is_active = False
        self.membership.save(update_fields=["is_active"])
        self.assertEqual(self.client.get("/api/v1/staffs/me/").status_code, 401)
        self.ensure(status=401)
        self.assertEqual(self.counts(), (0, 0, 0))

    def test_tenant_token_mismatch_and_explicit_target_are_denied(self):
        other = Tenant.objects.create(code="staff-me-other", name="Other")
        TenantDomain.objects.create(tenant=other, host="staff-me-other.test", is_primary=False)
        foreign_client = self.client_for(self.owner, host="staff-me-other.test")
        self.assertEqual(foreign_client.post("/api/v1/staffs/me/", {}, format="json").status_code, 401)
        self.ensure(status=400, data={"tenant_id": other.pk, "user_id": self.owner.pk})
        self.assertEqual(self.counts(), (0, 0, 0))

    def test_conflict_savepoint_does_not_poison_foreign_link_recovery(self):
        other = Tenant.objects.create(code="staff-me-race", name="Foreign")
        foreign = Staff.objects.create(tenant=other, user=self.owner, name="Foreign", phone="")
        real_create = Staff.objects.create
        calls = 0

        def race_once(**kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise IntegrityError("synthetic insertion conflict")
            return real_create(**kwargs)

        with patch("apps.domains.staffs.services.identity.Staff.objects.create", side_effect=race_once):
            self.ensure()
        self.assertEqual(calls, 2)
        foreign.refresh_from_db()
        self.assertEqual(foreign.user_id, self.owner.pk)
        self.assertIsNone(Staff.objects.get(tenant=self.tenant).user_id)


@skipUnless(connection.vendor == "postgresql", "PostgreSQL row-lock verification")
class StaffMeConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="concurrent-owner", name="Concurrent Owner", is_staff=True,
        )
        self.tenants = []
        for index in range(2):
            tenant = Tenant.objects.create(code=f"owner-lock-{index}", name="Owner lock")
            TenantDomain.objects.create(tenant=tenant, host=f"owner-lock-{index}.test", is_primary=False)
            TenantMembership.objects.create(tenant=tenant, user=self.user, role="owner", is_active=True)
            self.tenants.append(tenant)

    def run_pair(self, indices):
        barrier = Barrier(2)

        def request(index):
            try:
                token = AccessToken.for_user(self.user)
                token["tenant_id"] = self.tenants[index].pk
                token["token_version"] = self.user.token_version
                client = APIClient(HTTP_HOST=f"owner-lock-{index}.test")
                client.credentials(HTTP_AUTHORIZATION="Bearer " + str(token))
                barrier.wait(timeout=10)
                response = client.post("/api/v1/staffs/me/", {}, format="json")
                return response.status_code, response.data
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(request, index) for index in indices]
            return [future.result(timeout=30) for future in futures]

    def test_same_tenant_concurrent_setup_converges(self):
        results = self.run_pair([0, 0])
        self.assertEqual([status for status, _ in results], [200, 200])
        self.assertEqual(results[0][1], results[1][1])
        self.assertEqual((Staff.objects.count(), WorkType.objects.count(), StaffWorkType.objects.count()), (1, 1, 1))

    def test_two_tenant_owner_global_link_race_preserves_winner(self):
        results = self.run_pair([0, 1])
        self.assertEqual([status for status, _ in results], [200, 200])
        self.assertTrue(all(data["is_owner"] and not data["owner_setup_required"] for _, data in results))
        self.assertEqual(Staff.objects.filter(user=self.user).count(), 1)
        self.assertEqual(Staff.objects.filter(user__isnull=True).count(), 1)
        self.assertEqual((Staff.objects.count(), WorkType.objects.count(), StaffWorkType.objects.count()), (2, 2, 2))
