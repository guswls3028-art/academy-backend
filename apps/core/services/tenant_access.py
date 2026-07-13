"""Canonical tenant authorization and primary-tenant reconciliation."""

from __future__ import annotations

from dataclasses import dataclass

from django.contrib.auth import get_user_model
from django.db import transaction

from academy.adapters.db.django import repositories_core as core_repo
STAFF_ROLES = ("owner", "admin", "staff", "teacher")
LIMITED_PROFILE_ROLES = ("student", "parent")


@dataclass(frozen=True)
class TenantAccessReconciliation:
    tenant_id: int | None
    user_deactivated: bool


class TenantAccessMutationError(ValueError):
    """A requested membership mutation would violate its role boundary."""


def get_active_membership_role(user, tenant) -> str | None:
    if not user or not tenant or not getattr(user, "is_active", False):
        return None
    membership = core_repo.membership_get(tenant=tenant, user=user, is_active=True)
    return membership.role if membership else None


def _role_profile_is_valid(*, user, tenant, role: str) -> bool:
    if role == "student":
        return core_repo.student_profile_exists_active(tenant=tenant, user=user)
    if role == "parent":
        return core_repo.parent_profile_exists(tenant=tenant, user=user)
    return role in STAFF_ROLES


def get_authorized_tenant_role(user, tenant) -> str | None:
    role = get_active_membership_role(user, tenant)
    if not role or not _role_profile_is_valid(user=user, tenant=tenant, role=role):
        return None
    return role


def user_has_active_tenant_access(user, tenant) -> bool:
    return get_authorized_tenant_role(user, tenant) is not None


def user_has_active_staff_access(user, tenant) -> bool:
    return get_authorized_tenant_role(user, tenant) in STAFF_ROLES


@transaction.atomic
def deactivate_tenant_membership(
    *,
    user,
    tenant,
    allowed_roles: tuple[str, ...] | None = None,
) -> TenantAccessReconciliation | None:
    """Deactivate one membership using the global User→membership lock order."""
    User = get_user_model()
    locked_user = User.objects.select_for_update().get(pk=user.pk)
    membership = core_repo.membership_get_for_update(tenant=tenant, user=locked_user)
    if not membership or not membership.is_active:
        return None
    if allowed_roles is not None and membership.role not in allowed_roles:
        raise TenantAccessMutationError(
            f"membership role '{membership.role}' cannot be changed by this lifecycle"
        )
    membership.is_active = False
    membership.save(update_fields=["is_active"])
    return reconcile_user_tenant_access(locked_user)


@transaction.atomic
def reconcile_user_tenant_access(
    user,
    *,
    login_identifier: str | None = None,
) -> TenantAccessReconciliation:
    """Invalidate old tokens and select a deterministic authorized default tenant.

    Call this immediately after a TenantMembership is removed or deactivated.
    The current tenant is retained when still authorized; otherwise the lowest
    tenant id with a valid role/profile becomes the default. The globally unique
    username remains stable; tenant-scoped login resolves through memberships.
    """
    User = get_user_model()
    locked_user = User.objects.select_for_update().get(pk=user.pk)
    memberships = core_repo.membership_list_active_for_user(locked_user)
    valid_memberships = [
        membership
        for membership in memberships
        if _role_profile_is_valid(
            user=locked_user,
            tenant=membership.tenant,
            role=membership.role,
        )
    ]
    valid_memberships.sort(
        key=lambda membership: (
            0 if membership.tenant_id == locked_user.tenant_id else 1,
            membership.tenant_id,
            membership.id,
        )
    )

    # Kept in the signature for rolling caller compatibility only. Identity is
    # no longer reconstructed when the preferred tenant changes.
    del login_identifier
    selected = valid_memberships[0] if valid_memberships else None

    locked_user.token_version = (locked_user.token_version or 0) + 1
    update_fields = ["token_version"]
    if selected is None:
        locked_user.tenant = None
        locked_user.is_active = False
        locked_user.is_staff = False
        update_fields.extend(["tenant", "is_active", "is_staff"])
        selected_tenant_id = None
    else:
        locked_user.tenant = selected.tenant
        locked_user.is_active = True
        # Django's global is_staff flag must follow the selected default tenant.
        # Staff access in any other tenant is granted only by TenantMembership.
        locked_user.is_staff = bool(
            locked_user.is_superuser or selected.role in STAFF_ROLES
        )
        update_fields.extend(["tenant", "is_active", "is_staff"])
        selected_tenant_id = selected.tenant_id

    locked_user.save(update_fields=update_fields)
    for field in ("tenant", "tenant_id", "is_active", "is_staff", "token_version"):
        setattr(user, field, getattr(locked_user, field))
    return TenantAccessReconciliation(
        tenant_id=selected_tenant_id,
        user_deactivated=not locked_user.is_active,
    )
