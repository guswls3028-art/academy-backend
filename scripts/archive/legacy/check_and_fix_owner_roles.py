#!/usr/bin/env python
"""
Check and fix owner roles for admin97 user across all tenants.
"""
import os
import sys
import django

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "apps.api.config.settings.dev")
django.setup()

from django.db import transaction
from django.contrib.auth import get_user_model
from apps.core.models import Tenant, TenantMembership
from academy.adapters.db.django import repositories_core as core_repo

User = get_user_model()

with transaction.atomic():
    user = User.objects.get(username="admin97")
    print(f"User: {user.username} ({user.name})")
    print("")
    
    # 모든 활성 테넌트 확인
    tenants = Tenant.objects.filter(is_active=True).order_by('id')
    print(f"Active tenants: {tenants.count()}")
    print("")
    
    for tenant in tenants:
        membership = TenantMembership.objects.filter(
            tenant=tenant,
            user=user,
            is_active=True
        ).first()
        
        if membership:
            print(f"Tenant {tenant.id} ({tenant.code}): role={membership.role}")
            # owner가 아니면 owner로 변경
            if membership.role != "owner":
                membership.role = "owner"
                membership.save(update_fields=['role'])
                print(f"  → Updated to owner")
        else:
            print(f"Tenant {tenant.id} ({tenant.code}): NO MEMBERSHIP")
            # 멤버십 생성
            membership = core_repo.membership_ensure_active(
                tenant=tenant,
                user=user,
                role="owner"
            )
            print(f"  → Created membership with role=owner")

print("")
print("✓ Done!")
