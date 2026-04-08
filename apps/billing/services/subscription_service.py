"""
SubscriptionService — 구독 상태 전이의 유일한 진입점.

상태 모델:
  subscription_status: active | grace | expired
  cancel_at_period_end: bool (해지 예약 플래그)
  canceled_at: datetime | null

허용 전이:
  active  → grace, expired
  grace   → active, expired
  expired → active

규칙:
  - 해지 예약(cancel_at_period_end)은 상태가 아니라 플래그.
    예약 순간에 subscription_status를 바꾸지 않는다.
  - 모든 전이는 select_for_update()로 보호.
  - BILLING_EXEMPT_TENANT_IDS 테넌트는 만료/유예 전이 대상에서 제외.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import TYPE_CHECKING

from django.conf import settings
from django.db import transaction
from django.utils import timezone

if TYPE_CHECKING:
    from apps.core.models.program import Program

logger = logging.getLogger(__name__)

VALID_TRANSITIONS: dict[str, set[str]] = {
    "active": {"grace", "expired"},
    "grace": {"active", "expired"},
    "expired": {"active"},
}


class SubscriptionTransitionError(Exception):
    """허용되지 않는 상태 전이 시도"""


def _validate_transition(current: str, target: str) -> None:
    allowed = VALID_TRANSITIONS.get(current, set())
    if target not in allowed:
        raise SubscriptionTransitionError(
            f"Invalid subscription transition: {current} → {target}"
        )


def _is_exempt(tenant_id: int) -> bool:
    return tenant_id in settings.BILLING_EXEMPT_TENANT_IDS


def _lock_program(program_id: int) -> "Program":
    """select_for_update로 Program 잠금. 트랜잭션 내에서만 호출."""
    from apps.core.models.program import Program
    return Program.objects.select_for_update().get(pk=program_id)


# ──────────────────────────────────────────────
# 상태 전이 함수
# ──────────────────────────────────────────────

@transaction.atomic
def renew(program_id: int, new_expires_at: date, *, next_billing_at: date | None = None) -> "Program":
    """
    결제 성공 또는 수동 연장 시 구독 갱신.
    active/grace/expired → active 전이 + 만료일 갱신.
    """
    program = _lock_program(program_id)

    if program.subscription_status != "active":
        _validate_transition(program.subscription_status, "active")

    program.subscription_status = "active"
    program.subscription_expires_at = new_expires_at
    if next_billing_at is not None:
        program.next_billing_at = next_billing_at
    # 갱신 시 해지 예약 해제
    program.cancel_at_period_end = False
    program.canceled_at = None

    program.save(update_fields=[
        "subscription_status",
        "subscription_expires_at",
        "next_billing_at",
        "cancel_at_period_end",
        "canceled_at",
        "updated_at",
    ])

    logger.info(
        "Subscription renewed: tenant=%s program=%s expires=%s",
        program.tenant_id, program.pk, new_expires_at,
    )
    return program


@transaction.atomic
def enter_grace(program_id: int) -> "Program":
    """
    결제 실패 누적 또는 미입금 초과 시 유예 기간 진입.
    active → grace 전이.
    """
    program = _lock_program(program_id)

    if _is_exempt(program.tenant_id):
        logger.warning("Skipping grace entry for exempt tenant %s", program.tenant_id)
        return program

    _validate_transition(program.subscription_status, "grace")

    program.subscription_status = "grace"
    program.save(update_fields=["subscription_status", "updated_at"])

    logger.warning(
        "Subscription entered grace: tenant=%s program=%s expires=%s",
        program.tenant_id, program.pk, program.subscription_expires_at,
    )
    return program


@transaction.atomic
def expire(program_id: int) -> "Program":
    """
    유예 기간 만료 또는 cancel_at_period_end 기간 종료 시 구독 만료.
    active/grace → expired 전이.
    """
    program = _lock_program(program_id)

    if _is_exempt(program.tenant_id):
        logger.warning("Skipping expiry for exempt tenant %s", program.tenant_id)
        return program

    _validate_transition(program.subscription_status, "expired")

    program.subscription_status = "expired"
    program.save(update_fields=["subscription_status", "updated_at"])

    logger.warning(
        "Subscription expired: tenant=%s program=%s",
        program.tenant_id, program.pk,
    )
    return program


@transaction.atomic
def schedule_cancel(program_id: int) -> "Program":
    """
    해지 예약 — subscription_status를 변경하지 않음.
    cancel_at_period_end=True, canceled_at=now() 설정.
    현재 기간 종료 시 expire()에서 실제 만료 처리.
    """
    program = _lock_program(program_id)

    if program.subscription_status == "expired":
        raise SubscriptionTransitionError(
            "Cannot schedule cancel: subscription already expired"
        )

    program.cancel_at_period_end = True
    program.canceled_at = timezone.now()
    program.save(update_fields=["cancel_at_period_end", "canceled_at", "updated_at"])

    logger.info(
        "Cancel scheduled: tenant=%s program=%s at_period_end=True",
        program.tenant_id, program.pk,
    )
    return program


@transaction.atomic
def revoke_cancel(program_id: int) -> "Program":
    """
    해지 예약 철회 — cancel_at_period_end=False, canceled_at=None.
    """
    program = _lock_program(program_id)

    program.cancel_at_period_end = False
    program.canceled_at = None
    program.save(update_fields=["cancel_at_period_end", "canceled_at", "updated_at"])

    logger.info(
        "Cancel revoked: tenant=%s program=%s",
        program.tenant_id, program.pk,
    )
    return program


@transaction.atomic
def change_plan(program_id: int, new_plan: str) -> "Program":
    """플랜 변경. 가격은 Program.save()에서 자동 동기화."""
    from apps.core.models.program import Program

    valid_plans = {c[0] for c in Program.Plan.choices}
    if new_plan not in valid_plans:
        raise ValueError(f"Invalid plan: {new_plan}. Must be one of {valid_plans}")

    program = _lock_program(program_id)
    program.plan = new_plan
    program.save(update_fields=["plan", "monthly_price", "updated_at"])

    logger.info(
        "Plan changed: tenant=%s program=%s plan=%s price=%s",
        program.tenant_id, program.pk, program.plan, program.monthly_price,
    )
    return program


@transaction.atomic
def extend(program_id: int, days: int) -> "Program":
    """
    수동 기간 연장. 현재 만료일 기준으로 days만큼 연장.
    만료일이 과거이면 오늘 기준으로 연장.
    """
    program = _lock_program(program_id)

    base_date = program.subscription_expires_at or date.today()
    if base_date < date.today():
        base_date = date.today()

    new_expires = base_date + timedelta(days=days)

    # 만료 상태였으면 active로 복원
    if program.subscription_status == "expired":
        program.subscription_status = "active"
    elif program.subscription_status == "grace":
        program.subscription_status = "active"

    program.subscription_expires_at = new_expires
    program.cancel_at_period_end = False
    program.canceled_at = None

    program.save(update_fields=[
        "subscription_status",
        "subscription_expires_at",
        "cancel_at_period_end",
        "canceled_at",
        "updated_at",
    ])

    logger.info(
        "Subscription extended: tenant=%s program=%s +%d days → %s",
        program.tenant_id, program.pk, days, new_expires,
    )
    return program
