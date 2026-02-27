#!/usr/bin/env python
"""
Copy superuser from tenant 1 to tenant 9999.
테넌트 1번의 슈퍼유저를 테넌트 9999에도 동일하게 생성.
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
from apps.core.models import Tenant, TenantMembership
from academy.adapters.db.django import repositories_core as core_repo

User = get_user_model()

with transaction.atomic():
    # 테넌트 1번 가져오기
    tenant1 = Tenant.objects.get(id=1)
    print(f"✓ Tenant 1: {tenant1.code} ({tenant1.name})")
    
    # 테넌트 9999 가져오기
    tenant9999 = Tenant.objects.get(code="9999")
    print(f"✓ Tenant 9999: {tenant9999.code} ({tenant9999.name})")
    
    # 테넌트 1번의 모든 슈퍼유저 멤버십 찾기
    memberships_1 = TenantMembership.objects.filter(
        tenant=tenant1,
        user__is_superuser=True,
        is_active=True
    ).select_related('user')
    
    print(f"\n테넌트 1번의 슈퍼유저 멤버십: {memberships_1.count()}개")
    
    for membership in memberships_1:
        user = membership.user
        print(f"\n  - {user.username} ({user.name}, role={membership.role})")
        
        # 테넌트 9999에 동일한 사용자가 멤버로 있는지 확인
        existing = TenantMembership.objects.filter(
            tenant=tenant9999,
            user=user,
            is_active=True
        ).first()
        
        if existing:
            print(f"    → 이미 테넌트 9999의 멤버입니다 (role={existing.role})")
            # 역할이 다르면 업데이트
            if existing.role != membership.role:
                existing.role = membership.role
                existing.save(update_fields=['role'])
                print(f"    → 역할을 {membership.role}로 업데이트했습니다")
        else:
            # 테넌트 9999에 멤버십 생성
            new_membership = core_repo.membership_ensure_active(
                tenant=tenant9999,
                user=user,
                role=membership.role
            )
            print(f"    → 테넌트 9999에 멤버십 생성 완료 (role={new_membership.role})")

print("\n✓ 완료! 테넌트 1번의 모든 슈퍼유저가 테넌트 9999에도 등록되었습니다.")
