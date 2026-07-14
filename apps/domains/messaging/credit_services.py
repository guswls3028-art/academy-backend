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


def reserve_notification_credits(
    *,
    notification_log_id: int,
    billing_tenant_id: int,
    amount: str | Decimal,
) -> Decimal:
    """
    Idempotently reserve credits for a claimed notification.

    The balance mutation and NotificationLog marker share one transaction, so
    a worker restart before the provider call cannot charge the same business
    dispatch twice.
    """
    from apps.domains.messaging.models import NotificationLog

    amt = Decimal(str(amount))
    if amt <= 0:
        return Tenant.objects.get(pk=billing_tenant_id).credit_balance
    with transaction.atomic():
        notification = NotificationLog.objects.select_for_update().get(pk=notification_log_id)
        expected_billing_tenant_id = int(
            notification.source_tenant_id or notification.tenant_id
        )
        if expected_billing_tenant_id != int(billing_tenant_id):
            raise ValueError("notification_billing_tenant_mismatch")
        if notification.amount_deducted == amt:
            return Tenant.objects.get(pk=billing_tenant_id).credit_balance
        if notification.amount_deducted != Decimal("0"):
            raise ValueError("notification_credit_reservation_mismatch")

        tenant = Tenant.objects.select_for_update().get(pk=billing_tenant_id)
        if tenant.credit_balance < amt:
            raise ValueError("insufficient_balance")
        tenant.credit_balance -= amt
        tenant.save(update_fields=["credit_balance"])
        notification.amount_deducted = amt
        notification.save(update_fields=["amount_deducted"])
        return tenant.credit_balance


def rollback_notification_credits(
    *,
    notification_log_id: int,
    billing_tenant_id: int,
) -> Decimal:
    """Idempotently release credits reserved on a claimed notification."""
    from apps.domains.messaging.models import NotificationLog

    with transaction.atomic():
        notification = NotificationLog.objects.select_for_update().get(pk=notification_log_id)
        expected_billing_tenant_id = int(
            notification.source_tenant_id or notification.tenant_id
        )
        if expected_billing_tenant_id != int(billing_tenant_id):
            raise ValueError("notification_billing_tenant_mismatch")
        amount = notification.amount_deducted
        tenant = Tenant.objects.select_for_update().get(pk=billing_tenant_id)
        if amount <= 0:
            return tenant.credit_balance
        tenant.credit_balance += amount
        tenant.save(update_fields=["credit_balance"])
        notification.amount_deducted = Decimal("0")
        notification.save(update_fields=["amount_deducted"])
        return tenant.credit_balance


def get_tenant_messaging_info(tenant_id: int) -> Optional[dict]:
    """워커/API용: 테넌트 메시징 정보 (잔액, PFID, 발신번호, 단가).
    messaging_is_active는 표시용 반환만 하며, 발송 차단 정책에는 미사용(policy.can_send_sms 등 기준)."""
    t = Tenant.objects.filter(pk=tenant_id).values(
        "is_active", "kakao_pfid", "credit_balance", "messaging_is_active", "messaging_base_price",
        "messaging_sender", "messaging_provider",
    ).first()
    if not t:
        return None
    sender = (t.get("messaging_sender") or "").strip()
    return {
        "tenant_is_active": bool(t["is_active"]),
        "kakao_pfid": t["kakao_pfid"] or None,
        "credit_balance": str(t["credit_balance"]),
        "is_active": t["messaging_is_active"],
        "base_price": str(t["messaging_base_price"]),
        "sender": sender if sender else None,
        "messaging_provider": (t.get("messaging_provider") or "solapi").strip(),
    }
