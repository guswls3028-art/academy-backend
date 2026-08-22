# PATH: apps/core/management/commands/dump_tenant_and_user.py
"""Print non-secret tenant account state for local diagnostics."""

from __future__ import annotations

import json

from django.core.management.base import BaseCommand, CommandError

from academy.adapters.db.django import repositories_core as core_repo
class Command(BaseCommand):
    help = "Print a tenant-scoped account status summary without credentials or PII."

    def add_arguments(self, parser):
        parser.add_argument(
            "--tenant-code",
            required=True,
            help="Exact active tenant code.",
        )
        parser.add_argument(
            "--username",
            required=True,
            help="Tenant-visible login username.",
        )

    def handle(self, *args, **options):
        tenant_code = str(options["tenant_code"] or "").strip()
        username = str(options["username"] or "").strip()
        if not tenant_code or not username:
            raise CommandError("--tenant-code and --username must be non-empty.")

        tenant = core_repo.tenant_get_by_code(tenant_code)
        if tenant is None:
            raise CommandError(f"Active tenant {tenant_code!r} was not found.")

        user = core_repo.user_get_by_tenant_username(tenant, username)
        if user is None:
            raise CommandError("User was not found inside the selected tenant.")

        program = core_repo.program_get_by_tenant(tenant)
        membership = core_repo.membership_get_full(tenant=tenant, user=user)
        domains = core_repo.tenant_domain_filter_by_tenant(tenant)

        output = {
            "tenant": {
                "id": tenant.id,
                "code": tenant.code,
                "is_active": tenant.is_active,
            },
            "tenant_domains": [
                {
                    "host": domain.host,
                    "is_primary": domain.is_primary,
                    "is_active": domain.is_active,
                }
                for domain in domains
            ],
            "program": {
                "exists": program is not None,
                "id": program.id if program is not None else None,
                "is_active": program.is_active if program is not None else None,
            },
            "user": {
                "id": user.id,
                "tenant_id": user.tenant_id,
                "is_active": user.is_active,
            },
            "membership": {
                "exists": membership is not None,
                "role": membership.role if membership is not None else None,
                "is_active": membership.is_active if membership is not None else None,
            },
        }
        self.stdout.write(json.dumps(output, ensure_ascii=False, indent=2))
