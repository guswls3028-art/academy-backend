import json
from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from rest_framework.test import APIRequestFactory, force_authenticate
from rest_framework_simplejwt.exceptions import AuthenticationFailed
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.tokens import AccessToken

from apps.api.common.auth_jwt import TenantAwareTokenObtainPairSerializer
from apps.core.authentication import TokenVersionJWTAuthentication
from apps.core.management.commands.audit_tenant_access import _confirmation_token
from apps.core.models import PendingPasswordReset, Program, Tenant, TenantDomain, TenantMembership
from apps.core.models.user import user_internal_username
from apps.core.permissions import TenantResolvedAndMember, TenantResolvedAndStaff
from apps.core.services.password import create_pending_password_reset
from apps.core.services.tenant_access import reconcile_user_tenant_access
from apps.core.tenant.context import clear_current_tenant
from apps.core.views.dev_tenant_ops import DevImpersonateView
from apps.core.views.program import SubscriptionView
from apps.core.views.tenant_management import TenantCreateView, TenantOwnerDetailView
from apps.domains.clinic.models import SessionParticipant
from apps.domains.clinic.views.participant_views import ParticipantViewSet
from apps.domains.parents.models import Parent
from apps.domains.staffs.models import Staff
from apps.domains.staffs.serializers import StaffCreateUpdateSerializer
from apps.domains.students.models import Student
from apps.domains.students.services.lifecycle import soft_delete_student
from apps.domains.teachers.models import Teacher


User = get_user_model()


def _tenant(code: str) -> Tenant:
    return Tenant.objects.create(code=code, name=code, is_active=True)


def _user(tenant: Tenant, identifier: str, *, password: str = "pw123456", **extra):
    return User.objects.create_user(
        username=user_internal_username(tenant, identifier),
        password=password,
        tenant=tenant,
        is_active=True,
        **extra,
    )


def _login_serializer(*, tenant: Tenant, identifier: str, password: str = "pw123456"):
    class RequestStub:
        META = {}
        data = {}

        @staticmethod
        def get_host():
            return "api.hakwonplus.com"

    return TenantAwareTokenObtainPairSerializer(
        data={
            "username": identifier,
            "password": password,
            "tenant_code": tenant.code,
        },
        context={"request": RequestStub()},
    )


class TenantMembershipAuthorizationRegressionTests(TestCase):
    def test_ensure_active_applies_requested_role_on_existing_membership(self):
        tenant = _tenant("membership-role-reapply")
        user = _user(tenant, "stale-privileged-role", is_staff=True)
        membership = TenantMembership.objects.create(
            tenant=tenant,
            user=user,
            role="owner",
            is_active=False,
        )

        ensured = TenantMembership.ensure_active(
            tenant=tenant,
            user=user,
            role="staff",
        )

        membership.refresh_from_db()
        self.assertEqual(ensured.id, membership.id)
        self.assertTrue(membership.is_active)
        self.assertEqual(membership.role, "staff")

        TenantMembership.ensure_active(tenant=tenant, user=user, role="student")
        membership.refresh_from_db()
        self.assertEqual(membership.role, "student")

    def test_username_collision_cannot_deactivate_valid_other_tenant_membership(self):
        tenant_a = _tenant("stable-identity-a")
        tenant_b = _tenant("stable-identity-b")
        user = _user(tenant_a, "same-login", is_staff=True)
        TenantMembership.ensure_active(tenant=tenant_a, user=user, role="teacher")
        TenantMembership.ensure_active(tenant=tenant_b, user=user, role="admin")
        _user(tenant_b, "same-login")  # old reconciliation collided with this username
        TenantMembership.objects.filter(tenant=tenant_a, user=user).update(is_active=False)

        result = reconcile_user_tenant_access(user)

        user.refresh_from_db()
        self.assertEqual(result.tenant_id, tenant_b.id)
        self.assertTrue(user.is_active)
        self.assertEqual(user.tenant_id, tenant_b.id)
        self.assertEqual(user.username, user_internal_username(tenant_a, "same-login"))

    def test_reconciliation_never_selects_membership_of_inactive_tenant(self):
        active_tenant = _tenant("reconcile-active-tenant")
        inactive_tenant = _tenant("reconcile-inactive-tenant")
        inactive_tenant.is_active = False
        inactive_tenant.save(update_fields=["is_active"])
        user = _user(active_tenant, "inactive-tenant-member", is_staff=True)
        TenantMembership.ensure_active(
            tenant=active_tenant,
            user=user,
            role="teacher",
        )
        TenantMembership.ensure_active(
            tenant=inactive_tenant,
            user=user,
            role="admin",
        )
        TenantMembership.objects.filter(
            tenant=active_tenant,
            user=user,
        ).update(is_active=False)

        result = reconcile_user_tenant_access(user)

        user.refresh_from_db()
        self.assertIsNone(result.tenant_id)
        self.assertTrue(result.user_deactivated)
        self.assertFalse(user.is_active)
        self.assertIsNone(user.tenant_id)

    def test_multi_tenant_staff_login_binds_requested_membership_tenant(self):
        tenant_a = _tenant("auth-switch-a")
        tenant_b = _tenant("auth-switch-b")
        user = _user(tenant_a, "shared-staff", is_staff=True)
        TenantMembership.ensure_active(tenant=tenant_a, user=user, role="admin")
        TenantMembership.ensure_active(tenant=tenant_b, user=user, role="teacher")

        serializer = _login_serializer(tenant=tenant_b, identifier="shared-staff")

        self.assertTrue(serializer.is_valid(), serializer.errors)
        token = AccessToken(serializer.validated_data["access"])
        self.assertEqual(token["tenant_id"], tenant_b.id)
        user.refresh_from_db()
        self.assertEqual(user.tenant_id, tenant_a.id)

    def test_duplicate_login_identifier_is_disambiguated_by_password(self):
        tenant = _tenant("auth-duplicate-identifier")
        owner = _user(
            tenant,
            "01012345678",
            password="owner-password",
            is_staff=True,
        )
        TenantMembership.ensure_active(tenant=tenant, user=owner, role="owner")
        parent_user = User.objects.create_user(
            username=f"p_{tenant.id}_01012345678",
            password="parent-password",
            tenant=tenant,
            is_active=True,
        )
        TenantMembership.ensure_active(tenant=tenant, user=parent_user, role="parent")
        Parent.objects.create(
            tenant=tenant,
            user=parent_user,
            name="Duplicate Identifier Parent",
            phone="01012345678",
        )

        owner_login = _login_serializer(
            tenant=tenant,
            identifier="01012345678",
            password="owner-password",
        )
        parent_login = _login_serializer(
            tenant=tenant,
            identifier="01012345678",
            password="parent-password",
        )

        self.assertTrue(owner_login.is_valid(), owner_login.errors)
        self.assertTrue(parent_login.is_valid(), parent_login.errors)
        self.assertEqual(
            AccessToken(owner_login.validated_data["access"])["user_id"],
            str(owner.id),
        )
        self.assertEqual(
            AccessToken(parent_login.validated_data["access"])["user_id"],
            str(parent_user.id),
        )

    def test_duplicate_login_identifier_with_shared_password_fails_closed(self):
        tenant = _tenant("auth-duplicate-shared-password")
        first = _user(tenant, "shared-login", password="same-password", is_staff=True)
        TenantMembership.ensure_active(tenant=tenant, user=first, role="owner")
        second = User.objects.create_user(
            username="shared-login",
            password="same-password",
            tenant=tenant,
            is_active=True,
            is_staff=True,
        )
        TenantMembership.ensure_active(tenant=tenant, user=second, role="admin")

        serializer = _login_serializer(
            tenant=tenant,
            identifier="shared-login",
            password="same-password",
        )

        self.assertFalse(serializer.is_valid())
        self.assertEqual(
            serializer.errors["detail"][0],
            "로그인 아이디 또는 비밀번호가 올바르지 않습니다.",
        )

    def test_duplicate_login_identifier_consumes_only_matching_pending_reset(self):
        tenant = _tenant("auth-duplicate-pending-reset")
        first = _user(tenant, "pending-login", password="first-password", is_staff=True)
        TenantMembership.ensure_active(tenant=tenant, user=first, role="owner")
        second = User.objects.create_user(
            username="pending-login",
            password="second-password",
            tenant=tenant,
            is_active=True,
            is_staff=True,
        )
        TenantMembership.ensure_active(tenant=tenant, user=second, role="admin")
        create_pending_password_reset(second, "5678")

        serializer = _login_serializer(
            tenant=tenant,
            identifier="pending-login",
            password="5678",
        )

        self.assertTrue(serializer.is_valid(), serializer.errors)
        second.refresh_from_db()
        self.assertTrue(second.check_password("5678"))
        self.assertTrue(second.must_change_password)
        self.assertFalse(PendingPasswordReset.objects.filter(user=second).exists())
        self.assertTrue(first.check_password("first-password"))

    def test_primary_tenant_pointer_without_membership_cannot_login_or_authorize(self):
        tenant = _tenant("auth-no-membership")
        user = _user(tenant, "orphan")

        serializer = _login_serializer(tenant=tenant, identifier="orphan")
        request = SimpleNamespace(user=user, tenant=tenant)

        self.assertFalse(serializer.is_valid())
        self.assertFalse(TenantResolvedAndMember().has_permission(request, None))
        self.assertFalse(TenantResolvedAndStaff().has_permission(request, None))

    def test_jwt_with_missing_or_inactive_claim_tenant_fails_closed(self):
        tenant = _tenant("inactive-token-tenant")
        user = _user(tenant, "inactive-token-user", is_staff=True)
        TenantMembership.ensure_active(tenant=tenant, user=user, role="admin")
        token = RefreshToken.for_user(user).access_token
        token["tenant_id"] = tenant.id
        token["token_version"] = user.token_version
        tenant.is_active = False
        tenant.save(update_fields=["is_active"])
        clear_current_tenant()

        with self.assertRaises(AuthenticationFailed) as ctx:
            TokenVersionJWTAuthentication().get_user(token)

        self.assertIn("비활성", str(ctx.exception))

    def test_access_token_derived_from_legacy_claimless_refresh_is_rejected(self):
        tenant = _tenant("legacy-refresh-claims")
        user = _user(tenant, "legacy-refresh-user")
        TenantMembership.ensure_active(tenant=tenant, user=user, role="teacher")
        legacy_access = RefreshToken.for_user(user).access_token
        clear_current_tenant()

        with self.assertRaises(AuthenticationFailed) as ctx:
            TokenVersionJWTAuthentication().get_user(legacy_access)

        self.assertIn("다시 로그인", str(ctx.exception.detail))

    def test_global_is_staff_follows_selected_primary_role_only(self):
        tenant_student = _tenant("primary-student-role")
        tenant_staff = _tenant("secondary-staff-role")
        user = _user(tenant_student, "dual-role", is_staff=True)
        Student.objects.create(
            tenant=tenant_student,
            user=user,
            ps_number="DUAL-ROLE",
            omr_code="87654321",
            name="Dual Role",
            parent_phone="01087654321",
        )
        TenantMembership.ensure_active(tenant=tenant_student, user=user, role="student")
        TenantMembership.ensure_active(tenant=tenant_staff, user=user, role="admin")

        reconcile_user_tenant_access(user)

        user.refresh_from_db()
        self.assertEqual(user.tenant_id, tenant_student.id)
        self.assertFalse(user.is_staff)
        self.assertTrue(
            TenantResolvedAndStaff().has_permission(
                SimpleNamespace(user=user, tenant=tenant_staff),
                None,
            )
        )

    def test_staff_revocation_reconciles_primary_tenant_and_blocks_removed_tenant(self):
        tenant_a = _tenant("staff-revoke-a")
        tenant_b = _tenant("staff-revoke-b")
        user = _user(tenant_a, "revoked-staff", is_staff=True)
        TenantMembership.ensure_active(tenant=tenant_a, user=user, role="teacher")
        TenantMembership.ensure_active(tenant=tenant_b, user=user, role="admin")
        staff = Staff.objects.create(
            tenant=tenant_a,
            user=user,
            name="Revoked Staff",
            phone="01011112222",
        )

        StaffCreateUpdateSerializer(
            staff,
            context={"request": SimpleNamespace(tenant=tenant_a, user=user)},
        ).delete(staff)

        user.refresh_from_db()
        self.assertTrue(user.is_active)
        self.assertTrue(user.is_staff)
        self.assertEqual(user.tenant_id, tenant_b.id)
        self.assertEqual(user.username, user_internal_username(tenant_a, "revoked-staff"))
        self.assertEqual(user.token_version, 1)
        self.assertFalse(
            TenantMembership.objects.get(tenant=tenant_a, user=user).is_active
        )
        self.assertFalse(
            TenantResolvedAndStaff().has_permission(
                SimpleNamespace(user=user, tenant=tenant_a),
                None,
            )
        )
        self.assertTrue(
            TenantResolvedAndStaff().has_permission(
                SimpleNamespace(user=user, tenant=tenant_b),
                None,
            )
        )
        self.assertFalse(_login_serializer(tenant=tenant_a, identifier="revoked-staff").is_valid())
        login_b = _login_serializer(tenant=tenant_b, identifier="revoked-staff")
        self.assertTrue(login_b.is_valid(), login_b.errors)

    def test_student_revocation_keeps_other_role_but_denies_deleted_primary_tenant(self):
        tenant_a = _tenant("student-revoke-a")
        tenant_b = _tenant("student-revoke-b")
        user = _user(tenant_a, "revoked-student", phone="01022223333")
        student = Student.objects.create(
            tenant=tenant_a,
            user=user,
            ps_number="REVOKED-STUDENT",
            omr_code="22223333",
            name="Revoked Student",
            phone="01022223333",
            parent_phone="01099998888",
        )
        TenantMembership.ensure_active(tenant=tenant_a, user=user, role="student")
        TenantMembership.ensure_active(tenant=tenant_b, user=user, role="admin")

        result = soft_delete_student(student, tenant=tenant_a)

        user.refresh_from_db()
        self.assertFalse(result.user_deactivated)
        self.assertTrue(user.is_active)
        self.assertEqual(user.tenant_id, tenant_b.id)
        self.assertEqual(user.username, user_internal_username(tenant_a, "revoked-student"))
        self.assertEqual(user.phone, "01022223333")
        self.assertEqual(user.token_version, 1)
        self.assertFalse(
            TenantMembership.objects.get(tenant=tenant_a, user=user).is_active
        )
        self.assertFalse(
            TenantResolvedAndMember().has_permission(
                SimpleNamespace(user=user, tenant=tenant_a),
                None,
            )
        )
        self.assertTrue(
            TenantResolvedAndStaff().has_permission(
                SimpleNamespace(user=user, tenant=tenant_b),
                None,
            )
        )


class ClinicLimitedRoleFailClosedTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.tenant = _tenant("clinic-profile-guard")

    def _list(self, user):
        request = self.factory.get("/api/v1/clinic/participants/")
        request.tenant = self.tenant
        force_authenticate(request, user=user)
        return ParticipantViewSet.as_view({"get": "list"})(request)

    def test_student_membership_without_active_profile_is_denied_before_query(self):
        user = _user(self.tenant, "broken-student")
        TenantMembership.ensure_active(tenant=self.tenant, user=user, role="student")
        before = SessionParticipant.objects.count()

        response = self._list(user)

        self.assertEqual(response.status_code, 403, response.data)
        self.assertEqual(SessionParticipant.objects.count(), before)

    def test_parent_membership_without_resolvable_child_is_denied(self):
        user = _user(self.tenant, "parent-no-child")
        TenantMembership.ensure_active(tenant=self.tenant, user=user, role="parent")
        Parent.objects.create(
            tenant=self.tenant,
            user=user,
            name="Parent Without Child",
            phone="01033334444",
        )

        response = self._list(user)

        self.assertEqual(response.status_code, 403, response.data)


class SubscriptionMetadataAuthorizationTests(TestCase):
    def test_non_staff_member_receives_entitlement_only(self):
        tenant = _tenant("subscription-member-scope")
        user = _user(tenant, "subscription-student")
        Student.objects.create(
            tenant=tenant,
            user=user,
            ps_number="SUB-STUDENT",
            omr_code="12345678",
            name="Subscription Student",
            parent_phone="01012345678",
        )
        TenantMembership.ensure_active(tenant=tenant, user=user, role="student")
        request = APIRequestFactory().get("/api/v1/core/subscription/")
        request.tenant = tenant
        force_authenticate(request, user=user)

        response = SubscriptionView.as_view()(request)

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(
            set(response.data),
            {"is_subscription_active", "tenant_code", "tenant_name"},
        )


class DevImpersonationTenantBindingTests(TestCase):
    def test_token_uses_validated_requested_tenant_not_target_primary_tenant(self):
        platform = _tenant("platform-owner")
        requested = _tenant("impersonate-requested")
        primary = _tenant("impersonate-primary")
        actor = _user(platform, "platform-admin", is_superuser=True, is_staff=True)
        TenantMembership.ensure_active(tenant=platform, user=actor, role="owner")
        target = _user(primary, "multi-role-target", is_staff=True)
        TenantMembership.ensure_active(tenant=primary, user=target, role="parent")
        Parent.objects.create(
            tenant=primary,
            user=target,
            name="Private Parent",
            phone="01055556666",
        )
        TenantMembership.ensure_active(tenant=requested, user=target, role="teacher")
        request = APIRequestFactory().post(
            f"/api/v1/core/dev/tenants/{requested.id}/impersonate/",
            {"user_id": target.id},
            format="json",
        )
        request.tenant = platform
        force_authenticate(request, user=actor)

        with override_settings(OWNER_TENANT_ID=platform.id):
            response = DevImpersonateView.as_view()(request, tenant_id=requested.id)

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(AccessToken(response.data["access"])["tenant_id"], requested.id)
        self.assertEqual(response.data["target"]["tenant_id"], requested.id)


class StaffMembershipLifecycleTests(TestCase):
    def setUp(self):
        self.tenant = _tenant("staff-membership-lifecycle")
        self.user = _user(self.tenant, "lifecycle-teacher", is_staff=True)
        TenantMembership.ensure_active(tenant=self.tenant, user=self.user, role="teacher")
        self.staff = Staff.objects.create(
            tenant=self.tenant,
            user=self.user,
            name="Lifecycle Teacher",
            phone="01077778888",
        )

    def _serializer(self, data):
        return StaffCreateUpdateSerializer(
            self.staff,
            data=data,
            partial=True,
            context={"request": SimpleNamespace(tenant=self.tenant, user=self.user)},
        )

    def test_soft_deactivation_revokes_membership_and_reactivation_requires_role(self):
        serializer = self._serializer({"is_active": False})
        serializer.is_valid(raise_exception=True)
        serializer.save()

        self.user.refresh_from_db()
        self.staff.refresh_from_db()
        membership = TenantMembership.objects.get(tenant=self.tenant, user=self.user)
        self.assertFalse(self.staff.is_active)
        self.assertFalse(membership.is_active)
        self.assertFalse(self.user.is_active)
        self.assertEqual(self.user.token_version, 1)

        missing_role = self._serializer({"is_active": True})
        missing_role.is_valid(raise_exception=True)
        with self.assertRaisesMessage(Exception, "역할"):
            missing_role.save()

        reactivate = self._serializer({"is_active": True, "role": "TEACHER"})
        reactivate.is_valid(raise_exception=True)
        reactivate.save()

        self.user.refresh_from_db()
        self.staff.refresh_from_db()
        membership.refresh_from_db()
        self.assertTrue(self.staff.is_active)
        self.assertTrue(self.user.is_active)
        self.assertTrue(membership.is_active)
        self.assertEqual(membership.role, "teacher")
        self.assertEqual(self.user.token_version, 2)

    def test_admin_membership_is_not_mutated_by_staff_lifecycle(self):
        TenantMembership.ensure_active(tenant=self.tenant, user=self.user, role="admin")
        serializer = self._serializer({"is_active": False})
        serializer.is_valid(raise_exception=True)

        with self.assertRaisesMessage(Exception, "관리자"):
            serializer.save()

        self.staff.refresh_from_db()
        membership = TenantMembership.objects.get(tenant=self.tenant, user=self.user)
        self.assertTrue(self.staff.is_active)
        self.assertTrue(membership.is_active)
        self.assertEqual(membership.role, "admin")

    def test_create_rejects_partial_login_credentials(self):
        for data in (
            {"username": "staff-only", "password": ""},
            {"username": "", "password": "pw123456"},
        ):
            serializer = StaffCreateUpdateSerializer(
                data={
                    "name": "Credential Pair",
                    "phone": "01088889999",
                    "role": "ASSISTANT",
                    **data,
                },
                context={"request": SimpleNamespace(tenant=self.tenant, user=self.user)},
            )
            with self.subTest(data=data):
                self.assertFalse(serializer.is_valid())
                self.assertIn("username", serializer.errors)
                self.assertIn("password", serializer.errors)

    def test_active_role_patch_updates_membership_teacher_and_tokens_atomically(self):
        to_assistant = self._serializer({"role": "ASSISTANT"})
        to_assistant.is_valid(raise_exception=True)
        to_assistant.save()

        membership = TenantMembership.objects.get(tenant=self.tenant, user=self.user)
        self.user.refresh_from_db()
        self.assertEqual(membership.role, "staff")
        self.assertEqual(self.user.token_version, 1)
        self.assertFalse(
            Teacher.objects.filter(
                tenant=self.tenant,
                name=self.staff.name,
                is_active=True,
            ).exists()
        )

        to_teacher = self._serializer({"role": "TEACHER"})
        to_teacher.is_valid(raise_exception=True)
        to_teacher.save()

        membership.refresh_from_db()
        self.user.refresh_from_db()
        self.assertEqual(membership.role, "teacher")
        self.assertEqual(self.user.token_version, 2)
        self.assertTrue(
            Teacher.objects.filter(
                tenant=self.tenant,
                name=self.staff.name,
                phone=self.staff.phone,
                is_active=True,
            ).exists()
        )


class TenantOwnerRemovalInvariantTests(TestCase):
    def setUp(self):
        self.platform = _tenant("owner-invariant-platform")
        self.target = _tenant("owner-invariant-target")
        self.actor = _user(self.platform, "platform-owner", is_staff=True)
        TenantMembership.ensure_active(
            tenant=self.platform,
            user=self.actor,
            role="owner",
        )
        self.target_owner = _user(self.target, "target-owner", is_staff=True)
        TenantMembership.ensure_active(
            tenant=self.target,
            user=self.target_owner,
            role="owner",
        )

    def _delete(self, tenant, user):
        request = APIRequestFactory().delete("/api/v1/core/tenants/owners/")
        request.tenant = self.platform
        force_authenticate(request, user=self.actor)
        with override_settings(OWNER_TENANT_ID=self.platform.id):
            return TenantOwnerDetailView.as_view()(
                request,
                tenant_id=tenant.id,
                user_id=user.id,
            )

    def test_self_removal_is_forbidden(self):
        response = self._delete(self.platform, self.actor)
        self.assertEqual(response.status_code, 409, response.data)

    def test_final_active_owner_cannot_be_removed(self):
        response = self._delete(self.target, self.target_owner)
        self.assertEqual(response.status_code, 409, response.data)
        self.assertEqual(response.data["detail"], "final_active_owner_required")

    def test_owner_removal_succeeds_when_another_active_owner_remains(self):
        replacement = _user(self.target, "replacement-owner", is_staff=True)
        TenantMembership.ensure_active(
            tenant=self.target,
            user=replacement,
            role="owner",
        )

        response = self._delete(self.target, self.target_owner)

        self.assertEqual(response.status_code, 204)
        self.assertFalse(
            TenantMembership.objects.get(
                tenant=self.target,
                user=self.target_owner,
            ).is_active
        )


class TenantProvisioningInvariantTests(TestCase):
    def setUp(self):
        self.platform = _tenant("tenant-create-platform")
        self.actor = _user(self.platform, "tenant-creator", is_staff=True)
        TenantMembership.ensure_active(
            tenant=self.platform,
            user=self.actor,
            role="owner",
        )

    def _post(self, data):
        request = APIRequestFactory().post(
            "/api/v1/core/tenants/",
            data,
            format="json",
        )
        request.tenant = self.platform
        force_authenticate(request, user=self.actor)
        with override_settings(OWNER_TENANT_ID=self.platform.id):
            return TenantCreateView.as_view()(request)

    def test_creation_normalizes_and_atomically_provisions_domain_and_program(self):
        response = self._post({
            "code": "  New-Academy  ",
            "name": "  New Academy  ",
            "domain": "  ACADEMY.Example.COM:443  ",
        })

        self.assertEqual(response.status_code, 201, response.data)
        tenant = Tenant.objects.get(code="new-academy")
        self.assertEqual(tenant.name, "New Academy")
        self.assertEqual(
            set(tenant.domains.values_list("host", flat=True)),
            {"new-academy", "academy.example.com"},
        )
        self.assertEqual(
            tenant.domains.get(is_primary=True).host,
            "academy.example.com",
        )
        self.assertFalse(tenant.domains.get(host="new-academy").is_primary)
        program = Program.objects.get(tenant=tenant)
        self.assertEqual(program.display_name, "New Academy")
        self.assertEqual(program.brand_key, "new-academy")
        self.assertEqual(program.plan, Program.Plan.MAX)

    def test_existing_domain_ownership_conflict_does_not_create_partial_tenant(self):
        existing = _tenant("domain-owner")

        response = self._post({
            "code": "conflicted-new-tenant",
            "name": "Conflicted",
            "domain": existing.code.upper(),
        })

        self.assertEqual(response.status_code, 409, response.data)
        self.assertEqual(response.data["detail"], "tenant_domain_conflict")
        self.assertFalse(Tenant.objects.filter(code="conflicted-new-tenant").exists())

    def test_program_bootstrap_failure_rolls_back_tenant_and_domain(self):
        with patch(
            "academy.adapters.db.django.repositories_core.program_get_or_create",
            side_effect=RuntimeError("program bootstrap failed"),
        ):
            with self.assertRaisesMessage(RuntimeError, "program bootstrap failed"):
                self._post({
                    "code": "rollback-tenant",
                    "name": "Rollback Tenant",
                    "domain": "rollback.example.com",
                })

        self.assertFalse(Tenant.objects.filter(code="rollback-tenant").exists())
        self.assertFalse(
            TenantDomain.objects.filter(
                host__in=("rollback-tenant", "rollback.example.com"),
            ).exists()
        )

    def test_invalid_code_and_domain_are_rejected_before_writes(self):
        cases = (
            ({"code": "bad_code", "name": "Bad", "domain": "ok.example.com"}, "code_invalid"),
            ({"code": "valid-code", "name": "Bad", "domain": "https://bad.example.com/x"}, "domain_invalid"),
        )
        for payload, detail in cases:
            with self.subTest(payload=payload):
                response = self._post(payload)
                self.assertEqual(response.status_code, 400, response.data)
                self.assertEqual(response.data["detail"], detail)


class TenantAccessAuditCommandTests(TestCase):
    def _broken_records(self):
        tenant = _tenant("tenant-access-audit")
        missing_user = _user(tenant, "missing-membership")
        Student.objects.create(
            tenant=tenant,
            user=missing_user,
            ps_number="AUDIT-MISSING",
            omr_code="11223344",
            name="Audit Missing",
            parent_phone="01011223344",
        )
        orphan_user = _user(tenant, "orphan-membership")
        orphan = TenantMembership.ensure_active(
            tenant=tenant,
            user=orphan_user,
            role="student",
        )
        return tenant, missing_user, orphan_user, orphan

    def test_dry_run_reports_exact_ids_without_writes(self):
        _, missing_user, orphan_user, orphan = self._broken_records()
        output = StringIO()

        call_command("audit_tenant_access", "--no-fail", stdout=output)

        report = json.loads(output.getvalue().strip())
        self.assertEqual(
            report["repair_plan"]["create_student_membership_for_user_ids"],
            [missing_user.id],
        )
        self.assertEqual(
            report["repair_plan"]["deactivate_orphan_student_membership_ids"],
            [orphan.id],
        )
        self.assertFalse(
            TenantMembership.objects.filter(user=missing_user, is_active=True).exists()
        )
        orphan.refresh_from_db()
        orphan_user.refresh_from_db()
        self.assertTrue(orphan.is_active)
        self.assertTrue(orphan_user.is_active)

    def test_execute_requires_exact_confirmation_and_backup_then_verifies(self):
        _, missing_user, orphan_user, orphan = self._broken_records()
        dry_output = StringIO()
        call_command("audit_tenant_access", "--no-fail", stdout=dry_output)
        token = json.loads(dry_output.getvalue().strip())["required_confirmation_token"]
        with self.assertRaises(CommandError):
            call_command(
                "audit_tenant_access",
                "--execute",
                "--confirm=wrong",
                "--backup-file=unused.json",
                stdout=StringIO(),
            )
        with self.assertRaisesMessage(CommandError, "absolute"):
            call_command(
                "audit_tenant_access",
                "--execute",
                f"--confirm={token}",
                "--backup-file=relative-backup.json",
                stdout=StringIO(),
            )

        with TemporaryDirectory() as directory:
            existing_backup = Path(directory) / "existing.json"
            existing_backup.write_text("operator-owned", encoding="utf-8")
            with self.assertRaisesMessage(CommandError, "already exists"):
                call_command(
                    "audit_tenant_access",
                    "--execute",
                    f"--confirm={token}",
                    f"--backup-file={existing_backup}",
                    stdout=StringIO(),
                )
            self.assertEqual(
                existing_backup.read_text(encoding="utf-8"),
                "operator-owned",
            )
            backup = Path(directory) / "tenant-access-before.json"
            call_command(
                "audit_tenant_access",
                "--execute",
                f"--confirm={token}",
                f"--backup-file={backup}",
                stdout=StringIO(),
            )
            self.assertTrue(backup.exists())

        self.assertTrue(
            TenantMembership.objects.filter(
                user=missing_user,
                tenant=missing_user.tenant,
                role="student",
                is_active=True,
            ).exists()
        )
        orphan.refresh_from_db()
        orphan_user.refresh_from_db()
        self.assertFalse(orphan.is_active)
        self.assertFalse(orphan_user.is_active)

    def test_execute_refuses_partial_repair_when_any_finding_is_unsupported(self):
        tenant = _tenant("tenant-access-unsupported")
        user = _user(tenant, "parent-without-profile")
        membership = TenantMembership.ensure_active(
            tenant=tenant,
            user=user,
            role="parent",
        )
        dry_output = StringIO()
        call_command("audit_tenant_access", "--no-fail", stdout=dry_output)
        token = json.loads(dry_output.getvalue().strip())["required_confirmation_token"]
        with TemporaryDirectory() as directory:
            backup = Path(directory) / "must-not-be-created.json"
            with self.assertRaisesMessage(CommandError, "does not cover"):
                call_command(
                    "audit_tenant_access",
                    "--execute",
                    f"--confirm={token}",
                    f"--backup-file={backup}",
                    stdout=StringIO(),
                )
            self.assertFalse(backup.exists())
        membership.refresh_from_db()
        user.refresh_from_db()
        self.assertTrue(membership.is_active)
        self.assertTrue(user.is_active)

    def test_confirmation_token_binds_exact_ids_not_only_counts(self):
        first = {
            "repair_plan": {
                "create_student_membership_for_user_ids": [101],
                "deactivate_orphan_student_membership_ids": [201, 202],
            }
        }
        replacement_same_counts = {
            "repair_plan": {
                "create_student_membership_for_user_ids": [102],
                "deactivate_orphan_student_membership_ids": [203, 204],
            }
        }

        first_token = _confirmation_token(first)
        replacement_token = _confirmation_token(replacement_same_counts)

        self.assertNotEqual(first_token, replacement_token)
        self.assertTrue(first_token.startswith("REPAIR_TENANT_ACCESS:1:2:"))
        self.assertEqual(len(first_token.rsplit(":", 1)[-1]), 64)
