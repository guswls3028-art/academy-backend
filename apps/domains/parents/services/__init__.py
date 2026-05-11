# PATH: apps/domains/parents/services.py
"""
학부모 계정 생성/연결 서비스
- 학생 생성 시 학부모 계정 자동 생성
- 학부모 ID = 학부모 전화번호
"""

from django.db import transaction
from django.contrib.auth import get_user_model

from apps.core.models import TenantMembership
from ..models import Parent


# 과거 정책: 모든 학부모가 동일 비번 "0000" 사용 → 학부모 전화번호만 알면 자녀 성적/출결 전체 열람.
# 신규 정책: 학부모 전화번호 마지막 4자리를 초기 비번으로 사용. must_change_password=True 로 첫 로그인
# 강제 변경 게이트(MustChangePasswordGate)와 결합해 운영.
PARENT_DEFAULT_PASSWORD = "0000"  # deprecated — 외부 import 호환용 상수. 신규 코드에서는 절대 쓰지 말 것.


def parent_initial_password(parent_phone: str) -> str:
    """학부모 초기 비번 SSOT — 전화번호 정규화 후 마지막 4자리. 4자 미만이면 fallback."""
    digits = "".join(ch for ch in str(parent_phone or "") if ch.isdigit())
    return digits[-4:] if len(digits) >= 4 else (digits or "0000")


def ensure_parent_for_student(
    *,
    tenant,
    parent_phone: str,
    student_name: str,
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
    initial_pw = parent_initial_password(parent_phone)

    parent = Parent.objects.filter(tenant=tenant, phone=parent_phone).first()

    if parent:
        if not parent.user_id:
            with transaction.atomic():
                # 기존 parent.name이 있으면 우선 사용 — 자녀 N명일 때 마지막 자녀 이름으로 덮이는 문제 회피.
                user_name = parent.name or f"{student_name} 학부모"
                user = User.objects.create_user(
                    username=parent_username,
                    phone=parent_phone,
                    name=user_name,
                    tenant=tenant,
                )
                user.set_password(initial_pw)
                user.must_change_password = True
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
            tenant=tenant,
        )
        user.set_password(initial_pw)
        user.must_change_password = True
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
