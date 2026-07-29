"""Provision one tenant without adding it to a hard-coded tenant list.

The command is intentionally idempotent. Re-running it repairs missing core
rows but does not overwrite an existing tenant's manual branding or owner
password.
"""

from __future__ import annotations

import os
import re

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from academy.adapters.db.django import repositories_core as core_repo
from apps.core.models import Program, Tenant, TenantDomain
from apps.core.models.user import user_internal_username, user_display_username


DEFAULT_FEATURE_FLAGS = {
    "student_app_enabled": True,
    "admin_enabled": True,
}


def _normalize_code(value: str) -> str:
    code = str(value or "").strip().lower()
    if not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,48}[a-z0-9])?", code):
        raise CommandError("code must contain only lowercase letters, numbers, and hyphens")
    return code


def _normalize_domain(value: str) -> str:
    domain = str(value or "").strip().lower().rstrip(".")
    if domain.startswith("www."):
        domain = domain[4:]
    if (
        not domain
        or "://" in domain
        or "/" in domain
        or ":" in domain
        or len(domain) > 255
        or ".." in domain
    ):
        raise CommandError("domain must be an apex hostname such as example.com")
    labels = domain.split(".")
    if len(labels) < 2 or any(
        not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
        for label in labels
    ):
        raise CommandError("domain must be an apex hostname such as example.com")
    return domain


class Command(BaseCommand):
    help = "Idempotently provision one Tenant, Program, apex/www domains, and optional owner."

    def add_arguments(self, parser):
        parser.add_argument("code", type=str)
        parser.add_argument("--tenant-id", type=int, required=True)
        parser.add_argument("--name", type=str, required=True)
        parser.add_argument("--domain", type=str, required=True)
        parser.add_argument("--login-title", type=str, default="")
        parser.add_argument("--login-subtitle", type=str, default="")
        parser.add_argument("--window-title", type=str, default="")
        parser.add_argument("--logo-url", type=str, default="")
        parser.add_argument("--primary-color", type=str, default="")
        parser.add_argument("--owner-username", type=str, default="")
        parser.add_argument("--owner-name", type=str, default="")
        parser.add_argument(
            "--owner-password-env",
            type=str,
            default="TENANT_OWNER_INITIAL_PASSWORD",
            help="Environment variable containing the initial owner password.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Validate and print the resulting identifiers, then roll back all writes.",
        )

    def handle(self, *args, **options):
        code = _normalize_code(options["code"])
        tenant_id = options["tenant_id"]
        if tenant_id <= 0:
            raise CommandError("tenant-id must be a positive integer")
        name = str(options["name"] or "").strip()
        if not name or len(name) > 255:
            raise CommandError("name is required and must be at most 255 characters")
        domain = _normalize_domain(options["domain"])
        owner_username = str(options["owner_username"] or "").strip()
        owner_name = str(options["owner_name"] or "").strip()
        password_env = str(options["owner_password_env"] or "").strip()

        with transaction.atomic():
            tenant = self._ensure_tenant(
                tenant_id=tenant_id,
                code=code,
                name=name,
            )
            self._ensure_domains(tenant=tenant, domain=domain)
            self._ensure_program(
                tenant=tenant,
                name=name,
                domain=domain,
                login_title=str(options["login_title"] or "").strip(),
                login_subtitle=str(options["login_subtitle"] or "").strip(),
                window_title=str(options["window_title"] or "").strip(),
                logo_url=str(options["logo_url"] or "").strip(),
                primary_color=str(options["primary_color"] or "").strip(),
            )
            owner = None
            if owner_username:
                owner = self._ensure_owner(
                    tenant=tenant,
                    username=owner_username,
                    name=owner_name,
                    password_env=password_env,
                )

            if options["dry_run"]:
                transaction.set_rollback(True)

        mode = "DRY RUN" if options["dry_run"] else "APPLIED"
        owner_label = user_display_username(owner) if owner else "not requested"
        self.stdout.write(
            self.style.SUCCESS(
                f"{mode}: tenant={tenant.code} id={tenant.id} "
                f"primary={domain} owner={owner_label}"
            )
        )

    def _ensure_tenant(self, *, tenant_id: int, code: str, name: str) -> Tenant:
        tenant = Tenant.objects.filter(code=code).first()
        if tenant:
            if tenant.id != tenant_id:
                raise CommandError(
                    f"tenant code '{code}' already uses id={tenant.id}, not id={tenant_id}"
                )
            return tenant

        occupied = Tenant.objects.filter(id=tenant_id).first()
        if occupied:
            raise CommandError(
                f"tenant id={tenant_id} is already used by code '{occupied.code}'"
            )
        tenant = Tenant.objects.create(
            id=tenant_id,
            code=code,
            name=name,
            is_active=True,
        )
        self.stdout.write(f"Tenant created: {tenant.code} (id={tenant.id})")
        return tenant

    def _ensure_domains(self, *, tenant: Tenant, domain: str) -> None:
        requested_hosts = (domain, f"www.{domain}")
        conflicts = TenantDomain.objects.filter(host__in=requested_hosts).exclude(
            tenant=tenant
        )
        if conflicts.exists():
            conflict = conflicts.first()
            raise CommandError(
                f"domain '{conflict.host}' already belongs to tenant '{conflict.tenant.code}'"
            )

        other_primary = (
            TenantDomain.objects.filter(tenant=tenant, is_primary=True)
            .exclude(host=domain)
            .first()
        )
        if other_primary:
            if other_primary.host != tenant.code:
                raise CommandError(
                    f"tenant '{tenant.code}' already has primary domain '{other_primary.host}'"
                )
            other_primary.is_primary = False
            other_primary.save(update_fields=["is_primary"])

        for host in requested_hosts:
            row, created = TenantDomain.objects.get_or_create(
                host=host,
                defaults={
                    "tenant": tenant,
                    "is_primary": host == domain,
                    "is_active": True,
                },
            )
            update_fields: list[str] = []
            if not row.is_active:
                row.is_active = True
                update_fields.append("is_active")
            should_be_primary = host == domain
            if row.is_primary != should_be_primary:
                row.is_primary = should_be_primary
                update_fields.append("is_primary")
            if update_fields:
                row.save(update_fields=update_fields)
            state = "created" if created else "exists"
            self.stdout.write(f"TenantDomain {state}: {host}")

    def _ensure_program(
        self,
        *,
        tenant: Tenant,
        name: str,
        domain: str,
        login_title: str,
        login_subtitle: str,
        window_title: str,
        logo_url: str,
        primary_color: str,
    ) -> None:
        program, created = core_repo.program_get_or_create(tenant, defaults={})
        update_fields: list[str] = []

        if created or program.display_name == "HakwonPlus":
            program.display_name = name
            update_fields.append("display_name")
        if created or program.brand_key == "hakwonplus":
            program.brand_key = tenant.code
            update_fields.append("brand_key")
        if program.login_variant != Program.LoginVariant.HAKWONPLUS:
            program.login_variant = Program.LoginVariant.HAKWONPLUS
            update_fields.append("login_variant")
        if program.plan != Program.Plan.ALL:
            program.plan = Program.Plan.ALL
            update_fields.append("plan")
        if not program.is_active:
            program.is_active = True
            update_fields.append("is_active")

        feature_flags = dict(program.feature_flags or {})
        merged_flags = {**DEFAULT_FEATURE_FLAGS, **feature_flags}
        if merged_flags != feature_flags:
            program.feature_flags = merged_flags
            update_fields.append("feature_flags")

        ui_config = dict(program.ui_config or {})
        desired_ui = {
            "login_title": login_title or name,
            "login_subtitle": login_subtitle or domain,
            "window_title": window_title or name,
        }
        if logo_url:
            desired_ui["logo_url"] = logo_url
        if primary_color:
            if not re.fullmatch(r"#[0-9a-fA-F]{6}", primary_color):
                raise CommandError("primary-color must use #RRGGBB format")
            desired_ui["primary_color"] = primary_color.lower()
        for key, value in desired_ui.items():
            if created or not ui_config.get(key) or ui_config.get(key) == "HakwonPlus 관리자 로그인":
                ui_config[key] = value
        if ui_config != (program.ui_config or {}):
            program.ui_config = ui_config
            update_fields.append("ui_config")

        if update_fields:
            program.save(update_fields=list(dict.fromkeys(update_fields)))
        self.stdout.write(f"Program {'created' if created else 'exists'}: {tenant.code}")

    def _ensure_owner(
        self,
        *,
        tenant: Tenant,
        username: str,
        name: str,
        password_env: str,
    ):
        user = core_repo.user_get_by_tenant_username(tenant, username)
        if not user:
            password = os.environ.get(password_env, "") if password_env else ""
            if len(password) < 4:
                raise CommandError(
                    f"new owner requires a password of at least 4 characters "
                    f"in environment variable '{password_env}'"
                )
            User = get_user_model()
            user = User.objects.create_user(
                username=user_internal_username(tenant, username),
                password=password,
                tenant=tenant,
                email="",
                name=name or username,
                phone="",
                must_change_password=True,
            )
            self.stdout.write(f"Owner user created: {username}")

        core_repo.membership_ensure_active(tenant=tenant, user=user, role="owner")
        if not (tenant.owner_name or "").strip():
            tenant.owner_name = (
                (getattr(user, "name", None) or "").strip()
                or user_display_username(user)
                or "원장"
            )[:100]
            tenant.save(update_fields=["owner_name"])
        self.stdout.write(f"Owner membership exists: {username}")
        return user
