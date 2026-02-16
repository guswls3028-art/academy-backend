#!/usr/bin/env python
"""
Fix membership for tenant 9999.
Run this inside the Django shell or via manage.py shell.
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
    # Get tenant 9999
    from apps.core.models import Tenant
    try:
        tenant = Tenant.objects.get(code="9999")
    except Tenant.DoesNotExist:
        print("✗ Tenant 9999 not found!")
        sys.exit(1)
    
    # Get user admin97
    try:
        user = User.objects.get(username="admin97")
    except User.DoesNotExist:
        print("✗ User admin97 not found!")
        sys.exit(1)
    
    # Ensure membership exists
    membership = core_repo.membership_ensure_active(tenant=tenant, user=user, role="owner")
    print(f"✓ Membership ensured: {user.username} @ {tenant.code} (role={membership.role}, active={membership.is_active})")
    
    # Verify
    exists = core_repo.membership_exists(tenant=tenant, user=user, is_active=True)
    print(f"✓ Verification: membership_exists = {exists}")

print("\n✓ Done! Membership fixed.")
