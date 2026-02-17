# apps/support/messaging/credit_services.py
"""
크레딧 충전·차감·롤백 (발송 실패 시 복구)
- 충전: 선생님이 결제 완료 후 credit_balance 증가
- 차감: 발송 전 잔액 체크 후 차감 (워커에서 호출)
- 롤백: 발송 실패 시 차감된 금액 복구
"""

from decimal import Decimal
from typing import Optional

from django.db import transaction

from apps.core.models import Tenant


def charge_credits(tenant_id: int, amount: str | Decimal) -> Decimal:
    """
    선불 충전. 결제 완료 후 호출.
    Returns: 새 잔액
    """
    amt = Decimal(str(amount))
    if amt <= 0:
        raise ValueError("amount must be positive")
    with transaction.atomic():
        tenant = Tenant.objects.select_for_update().get(pk=tenant_id)
        tenant.credit_balance += amt
        tenant.save(update_fields=["credit_balance"])
        return tenant.credit_balance


def deduct_credits(tenant_id: int, amount: str | Decimal) -> Decimal:
    """
    발송 전 차감. 잔액 부족 시 ValueError.
    Returns: 차감 후 잔액
    """
    amt = Decimal(str(amount))
    if amt <= 0:
        raise ValueError("amount must be positive")
    with transaction.atomic():
        tenant = Tenant.objects.select_for_update().get(pk=tenant_id)
        if tenant.credit_balance < amt:
            raise ValueError("insufficient_balance")
        tenant.credit_balance -= amt
        tenant.save(update_fields=["credit_balance"])
        return tenant.credit_balance


def rollback_credits(tenant_id: int, amount: str | Decimal) -> Decimal:
    """
    발송 실패 시 차감 롤백. 복구 후 잔액 반환.
    """
    amt = Decimal(str(amount))
    if amt <= 0:
        return Tenant.objects.get(pk=tenant_id).credit_balance
    with transaction.atomic():
        tenant = Tenant.objects.select_for_update().get(pk=tenant_id)
        tenant.credit_balance += amt
        tenant.save(update_fields=["credit_balance"])
        return tenant.credit_balance


def get_tenant_messaging_info(tenant_id: int) -> Optional[dict]:
    """워커/API용: 테넌트 메시징 정보 (잔액, PFID, 발신번호, 활성화, 단가)"""
    t = Tenant.objects.filter(pk=tenant_id).values(
        "kakao_pfid", "credit_balance", "messaging_is_active", "messaging_base_price",
        "messaging_sender",
    ).first()
    if not t:
        return None
    sender = (t.get("messaging_sender") or "").strip()
    return {
        "kakao_pfid": t["kakao_pfid"] or None,
        "credit_balance": str(t["credit_balance"]),
        "is_active": t["messaging_is_active"],
        "base_price": str(t["messaging_base_price"]),
        "sender": sender if sender else None,
    }
