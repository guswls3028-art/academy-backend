# PATH: apps/core/management/commands/ensure_dev_user.py
"""
로컬 개발용 테넌트 + 기존 유저(admin97 / 개발용) 비밀번호·멤버십 + localhost 도메인 채우기.

- Tenant(code=admin97) 없으면 생성
- Program(tenant 1:1) 없으면 생성
- localhost, 127.0.0.1 → 해당 테넌트로 TenantDomain 연결
- username=admin97 유저 있으면 비밀번호만 kjkszpj123으로 맞추고 TenantMembership(admin) 연결
  없으면 User 생성 (이름 개발용) + 비밀번호 + TenantMembership

사용 (이미 ID admin97 이름 개발용 유저 있을 때):
  python manage.py ensure_dev_user --tenant=admin97 --password=kjkszpj123 --username=admin97 --name=개발용
"""
from django.core.management.base import BaseCommand
from django.db import transaction
from django.contrib.auth import get_user_model

from academy.adapters.db.django import repositories_core as core_repo


def normalize_host(host: str) -> str:
    v = str(host or "").strip().lower()
    if not v:
        return ""
    return v.split(":")[0].strip()


class Command(BaseCommand):
    help = "Ensure dev tenant + admin user + localhost domain for local login (e.g. tenant=admin97, password=kjkszpj123)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--tenant",
            type=str,
            default="admin97",
            help="Tenant code (default: admin97)",
        )
        parser.add_argument(
            "--password",
            type=str,
            default="kjkszpj123",
            help="Password for the admin user (default: kjkszpj123)",
        )
        parser.add_argument(
            "--username",
            type=str,
            default=None,
            help="Login username (default: same as --tenant)",
        )
        parser.add_argument(
            "--name",
            type=str,
            default="개발용",
            help="Display name when creating user (default: 개발용)",
        )
        parser.add_argument(
            "--hosts",
            type=str,
            default="localhost,127.0.0.1",
            help="Comma-separated hosts to map to this tenant (default: localhost,127.0.0.1)",
        )

    def handle(self, *args, **options):
        tenant_code = (options["tenant"] or "admin97").strip()
        password = (options["password"] or "kjkszpj123").strip()
        username = (options["username"] or tenant_code).strip()
        display_name = (options["name"] or "개발용").strip()
        hosts_str = options["hosts"] or "localhost,127.0.0.1"
        hosts = [normalize_host(h) for h in hosts_str.split(",") if normalize_host(h)]

        from apps.core.models import Program

        User = get_user_model()

        with transaction.atomic():
            # 1) Tenant
            tenant, tenant_created = core_repo.tenant_get_or_create(
                tenant_code,
                defaults={"name": tenant_code, "is_active": True},
            )
            if tenant_created:
                self.stdout.write(self.style.SUCCESS(f"Created Tenant: code={tenant.code}, name={tenant.name}"))
            else:
                if not tenant.is_active:
                    tenant.is_active = True
                    tenant.save(update_fields=["is_active"])
                self.stdout.write(f"Tenant already exists: code={tenant.code}")

            # 2) Program (tenant 1:1)
            program, program_created = core_repo.program_get_or_create(
                tenant,
                defaults={
                    "display_name": "HakwonPlus",
                    "brand_key": "hakwonplus",
                    "login_variant": Program.LoginVariant.HAKWONPLUS,
                    "plan": Program.Plan.PREMIUM,
                    "feature_flags": {
                        "student_app_enabled": True,
                        "admin_enabled": True,
                        "attendance_hourly_rate": 15000,
                    },
                    "ui_config": {"login_title": "HakwonPlus 관리자 로그인", "login_subtitle": ""},
                    "is_active": True,
                },
            )
            if program_created:
                self.stdout.write(self.style.SUCCESS(f"Created Program for tenant {tenant.code}"))
            else:
                self.stdout.write(f"Program already exists for tenant {tenant.code}")

            # 3) TenantDomain (localhost, 127.0.0.1 → this tenant)
            for host in hosts:
                domain, dom_created = core_repo.tenant_domain_get_or_create_by_defaults(
                    host,
                    defaults={
                        "tenant": tenant,
                        "is_primary": False,
                        "is_active": True,
                    },
                )
                if dom_created:
                    self.stdout.write(self.style.SUCCESS(f"Created TenantDomain: {host} -> {tenant.code}"))
                else:
                    if domain.tenant_id != tenant.id:
                        domain.tenant = tenant
                        domain.is_active = True
                        domain.save()
                        self.stdout.write(self.style.WARNING(f"Updated TenantDomain: {host} -> {tenant.code}"))
                    else:
                        self.stdout.write(f"TenantDomain already exists: {host} -> {tenant.code}")

            # 4) User (기존 admin97 / 개발용 있으면 비밀번호만 맞추고, 없으면 생성)
            user, user_created = core_repo.user_get_or_create(
                username,
                defaults={
                    "is_active": True,
                    "is_staff": True,
                    "is_superuser": False,
                    "email": f"{username}@local.dev",
                    "name": display_name,
                },
            )
            user.set_password(password)
            user.is_active = True
            user.is_staff = True
            if user.name != display_name and not user_created:
                user.name = display_name
            user.save(update_fields=["password", "is_active", "is_staff", "name"])
            if user_created:
                self.stdout.write(self.style.SUCCESS(f"Created User: username={username}, name={display_name}"))
            else:
                self.stdout.write(self.style.SUCCESS(f"Updated User: username={username}, password set, name={getattr(user, 'name', display_name)}"))

            # 5) TenantMembership (admin)
            membership = core_repo.membership_ensure_active(tenant=tenant, user=user, role="admin")
            self.stdout.write(self.style.SUCCESS(f"TenantMembership: {user.username} @ {tenant.code} ({membership.role})"))

        self.stdout.write(
            self.style.SUCCESS(
                f"Done. Log in with username={username}, password={password} (tenant {tenant_code} via http://localhost:8000)"
            )
        )
