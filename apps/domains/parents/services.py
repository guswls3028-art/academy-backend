# PATH: apps/domains/parents/services.py
"""
학부모 계정 생성/연결 서비스
- 학생 생성 시 학부모 계정 자동 생성
- 학부모 ID = 학부모 전화번호
"""

from django.db import transaction
from django.contrib.auth import get_user_model

from apps.core.models import TenantMembership
from .models import Parent


def ensure_parent_for_student(
    *,
    tenant,
    parent_phone: str,
    student_name: str,
    parent_password: str,
) -> Parent:
    """
    학부모 전화번호로 Parent 조회 또는 생성
    - 없으면 User + Parent + TenantMembership 생성
    - 있으면 기존 Parent 반환 (User 없으면 생성)
    """
    parent_phone = str(parent_phone or "").strip()
    if not parent_phone:
        raise ValueError("학부모 전화번호는 필수입니다.")

    User = get_user_model()
    # tenant 내 유일한 학부모 식별: username = p_{tenant_id}_{phone}
    parent_username = f"p_{tenant.id}_{parent_phone}"

    parent = Parent.objects.filter(tenant=tenant, phone=parent_phone).first()

    if parent:
        if not parent.user_id:
            with transaction.atomic():
                user = User.objects.create_user(
                    username=parent_username,
                    phone=parent_phone,
                    name=f"{student_name} 학부모",
                )
                user.set_password(parent_password)
                user.save()
                parent.user = user
                parent.save(update_fields=["user"])
                TenantMembership.ensure_active(
                    tenant=tenant,
                    user=user,
                    role="parent",
                )
        return parent

    with transaction.atomic():
        if User.objects.filter(username=parent_username).exists():
            raise ValueError(f"학부모 전화번호 {parent_phone}가 이미 다른 학원에서 사용 중입니다.")

        user = User.objects.create_user(
            username=parent_username,
            phone=parent_phone,
            name=f"{student_name} 학부모",
        )
        user.set_password(parent_password)
        user.save()

        parent = Parent.objects.create(
            tenant=tenant,
            user=user,
            name=f"{student_name} 학부모",
            phone=parent_phone,
        )

        TenantMembership.ensure_active(
            tenant=tenant,
            user=user,
            role="parent",
        )

    return parent
