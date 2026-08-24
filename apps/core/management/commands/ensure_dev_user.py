# PATH: apps/core/management/commands/ensure_dev_user.py
"""Create or repair a local-only tenant admin without exposing credentials."""

from __future__ import annotations

import os

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from academy.adapters.db.django import repositories_core as core_repo
from apps.core.services.password import change_password


DEFAULT_PASSWORD_ENV = "ACADEMY_DEV_USER_PASSWORD"


def normalize_host(host: str) -> str:
    value = str(host or "").strip().lower()
    if not value:
        return ""
    return value.split(":")[0].strip()


class Command(BaseCommand):
    help = "Ensure a local development tenant, admin user, and localhost domains."

    def add_arguments(self, parser):
        parser.add_argument(
            "--tenant",
            type=str,
            required=True,
            help="Local tenant code to create or repair.",
        )
        parser.add_argument(
            "--password-env",
            type=str,
            default=DEFAULT_PASSWORD_ENV,
            help=(
                "Environment variable containing the admin password "
                f"(default: {DEFAULT_PASSWORD_ENV})."
            ),
        )
        parser.add_argument(
            "--username",
            type=str,
            default=None,
            help="Login username (default: same as --tenant).",
        )
        parser.add_argument(
            "--name",
            type=str,
            default="개발용",
            help="Display name when creating the user (default: 개발용).",
        )
        parser.add_argument(
            "--hosts",
            type=str,
            default="localhost,127.0.0.1",
            help="Comma-separated local hosts to map to the tenant.",
        )
    def handle(self, *args, **options):
        tenant_code = str(options["tenant"] or "").strip()
        username = str(options["username"] or tenant_code).strip()
        display_name = str(options["name"] or "개발용").strip()
        password_env = str(options["password_env"] or "").strip()
        password = os.environ.get(password_env) if password_env else None
        hosts_str = options["hosts"] or "localhost,127.0.0.1"
        hosts = [normalize_host(host) for host in hosts_str.split(",") if normalize_host(host)]
        if not tenant_code:
            raise CommandError("--tenant must be a non-empty local tenant code.")
        if not username:
            raise CommandError("--username must be non-empty.")
        if password is None:
            raise CommandError(
                f"Set {password_env} to a local-only password before running ensure_dev_user."
            )
        if len(password) < 4:
            raise CommandError(f"{password_env} must contain at least 4 characters.")

        db = settings.DATABASES.get("default", {})
        db_engine = str(db.get("ENGINE") or "").lower()
        db_host = normalize_host(str(db.get("HOST") or ""))
        is_sqlite = "sqlite" in db_engine
        is_local_db = is_sqlite or db_host in {"", "localhost", "127.0.0.1", "::1"}
        if not is_local_db:
            raise CommandError(
                "ensure_dev_user refused to run against a non-local database. "
                "Set DJANGO_SETTINGS_MODULE/.env.local to a local DB."
            )

        from apps.core.models import Program
        from apps.core.models.user import user_internal_username

        User = get_user_model()

        with transaction.atomic():
            tenant, tenant_created = core_repo.tenant_get_or_create(
                tenant_code,
                defaults={"name": tenant_code, "is_active": True},
            )
            if tenant_created:
                self.stdout.write(
                    self.style.SUCCESS(f"Created Tenant: code={tenant.code}, name={tenant.name}")
                )
            else:
                if not tenant.is_active:
                    tenant.is_active = True
                    tenant.save(update_fields=["is_active"])
                self.stdout.write(f"Tenant already exists: code={tenant.code}")

            program, program_created = core_repo.program_get_or_create(
                tenant,
                defaults={
                    "display_name": "HakwonPlus",
                    "brand_key": "hakwonplus",
                    "login_variant": Program.LoginVariant.HAKWONPLUS,
                    "plan": Program.Plan.ALL,
                    "feature_flags": {
                        "student_app_enabled": True,
                        "admin_enabled": True,
                        "attendance_hourly_rate": 15000,
                    },
                    "ui_config": {
                        "login_title": "HakwonPlus 관리자 로그인",
                        "login_subtitle": "",
                    },
                    "is_active": True,
                },
            )
            if program_created:
                self.stdout.write(self.style.SUCCESS(f"Created Program for tenant {tenant.code}"))
            else:
                self.stdout.write(f"Program already exists for tenant {tenant.code}")

            for host in hosts:
                domain, domain_created = core_repo.tenant_domain_get_or_create_by_defaults(
                    host,
                    defaults={
                        "tenant": tenant,
                        "is_primary": False,
                        "is_active": True,
                    },
                )
                if domain_created:
                    self.stdout.write(
                        self.style.SUCCESS(f"Created TenantDomain: {host} -> {tenant.code}")
                    )
                elif domain.tenant_id != tenant.id:
                    domain.tenant = tenant
                    domain.is_active = True
                    domain.save(update_fields=["tenant", "is_active"])
                    self.stdout.write(
                        self.style.WARNING(f"Updated TenantDomain: {host} -> {tenant.code}")
                    )
                else:
                    self.stdout.write(f"TenantDomain already exists: {host} -> {tenant.code}")

            internal_username = user_internal_username(tenant, username)
            user, user_created = core_repo.user_get_or_create(
                internal_username,
                defaults={
                    "tenant": tenant,
                    "is_active": True,
                    "is_staff": True,
                    "is_superuser": False,
                    "email": f"{username}@local.dev",
                    "name": display_name,
                },
            )
            if user_created:
                user.set_password(password)
                user.save(update_fields=["password"])
            else:
                change_password(user, password)

            user.tenant = tenant
            user.is_active = True
            user.is_staff = True
            user.name = display_name
            user.save(update_fields=["tenant", "is_active", "is_staff", "name"])
            action = "Created" if user_created else "Updated"
            self.stdout.write(
                self.style.SUCCESS(
                    f"{action} User: username={username} (stored={internal_username}), "
                    f"name={display_name}"
                )
            )

            membership = core_repo.membership_ensure_active(
                tenant=tenant,
                user=user,
                role="admin",
            )
            self.stdout.write(
                self.style.SUCCESS(
                    f"TenantMembership: {user.username} @ {tenant.code} ({membership.role})"
                )
            )

        self.stdout.write(
            self.style.SUCCESS(
                f"Done. Local account {username} is ready for tenant {tenant_code}; "
                f"password loaded from {password_env} and not printed."
            )
        )
