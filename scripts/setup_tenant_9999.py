#!/usr/bin/env python
"""
Setup tenant 9999 for local development.
Creates tenant with code="9999", localhost domain, and superuser (유현진, admin97, kjkszpj123).
"""
import os
import sys
import django

# Setup Django (same as manage.py)
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "apps.api.config.settings.dev")
django.setup()

from django.db import transaction
from django.contrib.auth import get_user_model
from academy.adapters.db.django import repositories_core as core_repo

User = get_user_model()

with transaction.atomic():
    # 1) Create/get Tenant 9999
    tenant, tenant_created = core_repo.tenant_get_or_create(
        "9999",
        defaults={"name": "Local Dev Tenant", "is_active": True},
    )
    if tenant_created:
        print(f"✓ Created Tenant: id={tenant.id}, code={tenant.code}, name={tenant.name}")
    else:
        print(f"✓ Tenant already exists: id={tenant.id}, code={tenant.code}")
        if not tenant.is_active:
            tenant.is_active = True
            tenant.save(update_fields=["is_active"])

    # 2) Create/get Program for tenant 9999
    from apps.core.models import Program
    program, program_created = core_repo.program_get_or_create(
        tenant,
        defaults={
            "display_name": "Local Dev",
            "brand_key": "local-dev",
            "login_variant": Program.LoginVariant.HAKWONPLUS,
            "plan": Program.Plan.PREMIUM,
            "feature_flags": {
                "student_app_enabled": True,
                "admin_enabled": True,
                "attendance_hourly_rate": 15000,
            },
            "ui_config": {"login_title": "Local Dev 로그인", "login_subtitle": ""},
            "is_active": True,
        },
    )
    if program_created:
        print(f"✓ Created Program for tenant {tenant.code}")
    else:
        print(f"✓ Program already exists for tenant {tenant.code}")

    # 3) Create TenantDomain for localhost (port 5174 -> host is "localhost")
    domain, dom_created = core_repo.tenant_domain_get_or_create_by_defaults(
        "localhost",
        defaults={
            "tenant": tenant,
            "is_primary": True,
            "is_active": True,
        },
    )
    if dom_created:
        print(f"✓ Created TenantDomain: localhost -> {tenant.code}")
    else:
        if domain.tenant_id != tenant.id:
            domain.tenant = tenant
            domain.is_active = True
            domain.is_primary = True
            domain.save()
            print(f"✓ Updated TenantDomain: localhost -> {tenant.code}")
        else:
            print(f"✓ TenantDomain already exists: localhost -> {tenant.code}")

    # 4) Create/get User (유현진, admin97)
    user, user_created = core_repo.user_get_or_create(
        "admin97",
        defaults={
            "is_active": True,
            "is_staff": True,
            "is_superuser": True,
            "email": "admin97@local.dev",
            "name": "유현진",
        },
    )
    user.set_password("kjkszpj123")
    user.is_active = True
    user.is_staff = True
    user.is_superuser = True
    if user.name != "유현진":
        user.name = "유현진"
    user.save(update_fields=["password", "is_active", "is_staff", "is_superuser", "name"])
    if user_created:
        print(f"✓ Created User: username=admin97, name=유현진, is_superuser=True")
    else:
        print(f"✓ Updated User: username=admin97, password set, name=유현진, is_superuser=True")

    # 5) Create TenantMembership (owner role for superuser)
    membership = core_repo.membership_ensure_active(tenant=tenant, user=user, role="owner")
    print(f"✓ TenantMembership: {user.username} @ {tenant.code} ({membership.role})")

print("\n✓ Done! Tenant 9999 setup complete.")
print(f"  - Tenant: id={tenant.id}, code={tenant.code}")
print(f"  - Domain: localhost -> {tenant.code}")
print(f"  - User: admin97 / kjkszpj123 (유현진, superuser)")
print(f"  - Login at: http://localhost:5174")
