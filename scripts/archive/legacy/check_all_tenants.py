#!/usr/bin/env python
"""
Check all tenants (active and inactive).
"""
import os
import sys
import django
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

# manage.py와 동일하게 .env 로드 (DB 연결용)
try:
    from dotenv import load_dotenv
    load_dotenv(BASE_DIR / ".env")
    load_dotenv(BASE_DIR / ".env.local")
except Exception:
    pass

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
