"""Prepare explicitly identified onboarding accounts from a private stdin pipe."""

import json
import secrets
import sys
from io import StringIO
from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Q

from academy.adapters.db.django import repositories_core as core_repo
from apps.core.management.commands.provision_tenant import _normalize_domain
from apps.core.models import OpsAuditLog, Program, Tenant, TenantDomain, TenantMembership
from apps.core.models.user import user_internal_username
from apps.core.services.login_identifier import normalize_login_identifier
from apps.core.views.tenant_management import TenantOwnerRegistrationSerializer
from apps.domains.parents.models import Parent
from apps.domains.parents.services import find_parent_account_user, parent_account_username
from apps.domains.students.models import Student
from apps.domains.students.serializers import StudentCreateSerializer
from apps.domains.students.services.creation import create_student_account
from apps.domains.students.services.identity import canonical_student_phone, normalize_student_phone


def require(condition):
    if not condition:
        raise CommandError("Input, account identity or onboarding state conflicts.")


def unique_object(items):
    result = dict(items)
    require(len(items) == len(result))
    return result


def fields(data, required, optional=()):
    require(isinstance(data, dict) and set(required) <= data.keys() <= set(required) | set(optional))
    require(all(isinstance(v, str) and v.strip() for v in data.values()))


def valid_role(tenant, user, role):
    return (
        user
        and user.tenant_id == tenant.pk
        and user.is_active
        and user.has_usable_password()
        and TenantMembership.objects.select_for_update()
        .filter(tenant=tenant, user=user, role=role, is_active=True)
        .first()
    )


def audit_creation(tenant, user, action):
    OpsAuditLog.objects.create(
        actor_username="management:prepare_tenant_accounts",
        action=action,
        target_tenant=tenant,
        target_user=user,
        payload={"created_user": True},
    )


def prepare_owner(tenant, raw):
    require(
        isinstance(raw, dict) and {"username", "password"} <= raw.keys() <= {"username", "password", "name", "phone"}
    )
    require(all(isinstance(raw[key], str) and raw[key].strip() for key in ("username", "password")))
    require(all(raw.get(key) is None or isinstance(raw[key], str) for key in ("name", "phone")))
    serializer = TenantOwnerRegistrationSerializer(data=raw, context={"tenant": tenant})
    require(serializer.is_valid())
    data = serializer.validated_data
    profile = {key: data[key] for key in ("name", "phone") if data.get(key)}
    username = data["username"]
    require(username == normalize_login_identifier(username))
    owners = list(TenantMembership.objects.select_for_update().filter(tenant=tenant, role="owner"))
    candidates = core_repo.user_list_by_tenant_login_identifier(tenant, username)
    direct = core_repo.user_get_by_tenant_username(tenant, username)
    ids = {user.pk for user in candidates}
    if direct:
        ids.add(direct.pk)
    User = get_user_model()
    if ids or owners:
        require(len(ids) == len(owners) == 1 and owners[0].user_id in ids)
        user = User.objects.select_for_update().get(pk=owners[0].user_id)
        require(
            valid_role(tenant, user, "owner") and all(getattr(user, key) == value for key, value in profile.items())
        )
        return "existing"
    user = User.objects.create_user(
        tenant=tenant,
        username=user_internal_username(tenant, username),
        name=profile.get("name", ""),
        phone=profile.get("phone", ""),
        password=data["password"],
        must_change_password=True,
    )
    core_repo.membership_ensure_active(tenant=tenant, user=user, role="owner")
    if not (tenant.owner_name or "").strip():
        tenant.owner_name = (profile.get("name") or username)[:100]
        tenant.save(update_fields=["owner_name"])
    audit_creation(tenant, user, "owner.register")
    return "created"


def prepare_student(tenant, raw):
    fields(raw, ("ps_number", "name", "parent_phone", "initial_password"), ("phone", "parent_initial_password"))
    require(all(4 <= len(raw[key]) <= 128 for key in ("initial_password", "parent_initial_password") if key in raw))
    login = normalize_login_identifier(raw["ps_number"])
    parent_phone = normalize_student_phone(
        raw["parent_phone"], required=True, field_name="parent_phone", field_label="phone"
    )
    phone = canonical_student_phone(phone=raw.get("phone"), parent_phone=parent_phone)
    name = raw["name"].strip()
    identity = Q(ps_number=login) | Q(name=name, parent_phone=parent_phone)
    if phone:
        identity |= Q(phone=phone)
    matches = list(Student.objects.select_for_update().filter(identity, tenant=tenant))
    parent_user = find_parent_account_user(tenant=tenant, parent_phone=parent_phone)
    if parent_user:
        parent_user = get_user_model().objects.select_for_update().get(pk=parent_user.pk)
    parent = Parent.objects.select_for_update().filter(tenant=tenant, phone=parent_phone).first()
    if parent or parent_user:
        require(
            parent
            and valid_role(tenant, parent_user, "parent")
            and parent.user_id == parent_user.pk
            and parent_user.username == parent_account_username(tenant=tenant, parent_phone=parent_phone)
            and {u.pk for u in core_repo.user_list_by_tenant_login_identifier(tenant, parent_phone)} == {parent_user.pk}
        )
    if matches:
        require(len(matches) == 1)
        student = matches[0]
        require(
            student.deleted_at is None
            and student.name == name
            and student.ps_number == login
            and student.phone == phone
            and student.parent_phone == parent_phone
            and parent
            and student.parent_id == parent.pk
            and student.user_id
        )
        user = get_user_model().objects.select_for_update().get(pk=student.user_id)
        require(
            valid_role(tenant, user, "student")
            and core_repo.user_get_by_tenant_username(tenant, login) == user
            and {u.pk for u in core_repo.user_list_by_tenant_login_identifier(tenant, login)} == {user.pk}
        )
        return "existing"
    serializer = StudentCreateSerializer(data=raw, context={"request": SimpleNamespace(tenant=tenant)})
    require(serializer.is_valid())
    data = dict(serializer.validated_data)
    password = data.pop("initial_password")
    parent_password = data.pop("parent_initial_password", None)
    parent_mode = "fixed" if parent_password else None
    if not parent_user and not parent_password and not (tenant.account_password_policy or {}).get("parent_mode"):
        parent_password = secrets.token_urlsafe(24)
        parent_mode = "fixed"
    result = create_student_account(
        tenant=tenant,
        student_data=data,
        password=password,
        parent_password=parent_password,
        password_mode="fixed",
        parent_password_mode=parent_mode,
        must_change_password=True,
        locked_user_ids={parent_user.pk} if parent_user else set(),
    )
    require(result.parent and (not parent_user or not result.parent_credentials_initialized))
    audit_creation(tenant, result.user, "student.register")
    if not parent_user:
        audit_creation(tenant, result.parent.user, "parent.register")
    return "created"


class Command(BaseCommand):
    help = "Prepare onboarding accounts using a private JSON stdin pipe."

    def add_arguments(self, parser):
        parser.add_argument("code")
        parser.add_argument("--tenant-id", type=int, required=True)
        parser.add_argument("--domain", required=True)
        parser.add_argument("--billing-mode", choices=("contract", "exempt"), required=True)
        parser.add_argument("--messaging-mode", choices=("disabled", "approved"), required=True)

    def handle(self, *args, **opts):
        try:
            require(not sys.stdin.isatty())
            source = sys.stdin.read(16385)
            require(len(source) <= 16384)
            body = json.loads(source, object_pairs_hook=unique_object)
            require(isinstance(body, dict) and set(body) in ({"owner"}, {"owner", "student"}))
            apex = opts["domain"].encode("idna").decode("ascii").lower()
            require(apex == _normalize_domain(apex))
            with transaction.atomic():
                tenant = Tenant.objects.select_for_update().get(pk=opts["tenant_id"], code=opts["code"], is_active=True)
                domains = list(
                    TenantDomain.objects.select_for_update()
                    .filter(
                        tenant=tenant,
                        host__in=(apex, "www." + apex),
                        is_active=True,
                    )
                    .values_list("host", "is_primary")
                )
                require(set(domains) == {(apex, True), ("www." + apex, False)})
                Program.objects.select_for_update().get(tenant=tenant)
                call_command(
                    "audit_tenant_onboarding",
                    tenant.code,
                    tenant_id=tenant.pk,
                    domain=apex,
                    billing_mode=opts["billing_mode"],
                    messaging_mode=opts["messaging_mode"],
                    stdout=StringIO(),
                    stderr=StringIO(),
                )
                owner = prepare_owner(tenant, body["owner"])
                student = prepare_student(tenant, body["student"]) if "student" in body else "not_requested"
        except Exception:
            # ORM and serializer exceptions may contain identifiers, credentials or SQL.
            raise CommandError("TENANT_ACCOUNT_PREPARATION_FAILED") from None
        self.stdout.write(f"TENANT_ACCOUNTS_READY owner={owner} student={student}")
