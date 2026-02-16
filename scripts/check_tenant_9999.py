#!/usr/bin/env python
"""
Check tenant 9999 setup for local development.
Verifies tenant, domain, user, and membership.
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

from apps.core.models import Tenant, TenantDomain, TenantMembership
from django.contrib.auth import get_user_model

User = get_user_model()

print("=" * 60)
print("Checking Tenant 9999 Setup")
print("=" * 60)

# Check Tenant
try:
    tenant = Tenant.objects.get(code="9999")
    print(f"✓ Tenant 9999: id={tenant.id}, active={tenant.is_active}")
except Tenant.DoesNotExist:
    print("✗ Tenant 9999 does not exist!")
    sys.exit(1)

# Check Domain
domain = TenantDomain.objects.filter(host="localhost").first()
if domain:
    print(f"✓ Domain localhost: -> tenant {domain.tenant.id} ({domain.tenant.code}), active={domain.is_active}, primary={domain.is_primary}")
    if domain.tenant_id != tenant.id:
        print(f"  ⚠ WARNING: localhost domain is linked to tenant {domain.tenant.code}, not 9999!")
else:
    print("✗ Domain localhost does not exist!")

# Check User
try:
    user = User.objects.get(username="admin97")
    print(f"✓ User admin97: superuser={user.is_superuser}, active={user.is_active}, name={user.name}")
except User.DoesNotExist:
    print("✗ User admin97 does not exist!")
    sys.exit(1)

# Check Membership
membership = TenantMembership.objects.filter(tenant=tenant, user=user, is_active=True).first()
if membership:
    print(f"✓ Membership: {user.username} @ {tenant.code} (role={membership.role}, active={membership.is_active})")
else:
    print(f"✗ Membership not found: {user.username} @ {tenant.code}")
    print("  Creating membership...")
    from academy.adapters.db.django import repositories_core as core_repo
    membership = core_repo.membership_ensure_active(tenant=tenant, user=user, role="owner")
    print(f"✓ Created Membership: {user.username} @ {tenant.code} (role={membership.role})")

print("\n" + "=" * 60)
print("Summary:")
print(f"  - Tenant: {tenant.code} (id={tenant.id}, active={tenant.is_active})")
print(f"  - Domain: localhost -> {domain.tenant.code if domain else 'NOT FOUND'} (active={domain.is_active if domain else False})")
print(f"  - User: {user.username} (superuser={user.is_superuser}, active={user.is_active})")
print(f"  - Membership: {membership.role if membership else 'NOT FOUND'} (active={membership.is_active if membership else False})")
print("=" * 60)
