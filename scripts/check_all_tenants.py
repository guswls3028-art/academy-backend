#!/usr/bin/env python
"""
Check all tenants (active and inactive).
"""
import os
import sys
import django

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "apps.api.config.settings.dev")
django.setup()

from apps.core.models import Tenant

print("All tenants:")
print("=" * 60)

tenants = Tenant.objects.all().order_by('id')
for tenant in tenants:
    status = "ACTIVE" if tenant.is_active else "INACTIVE"
    print(f"ID: {tenant.id:2d} | Code: {tenant.code:15s} | Name: {tenant.name:30s} | {status}")

print("")
print(f"Total: {tenants.count()} tenants")
