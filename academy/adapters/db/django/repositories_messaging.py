"""
Messaging 도메인 DB 기록 — .objects. 접근을 adapters 내부로 한정 (Gate 7).
"""
from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.utils import timezone as tz


ACCOUNT_NOTIFICATION_TYPES = (
    "registration_approved_student",
    "registration_approved_parent",
    "password_find_otp",
    "password_reset_student",
    "password_reset_parent",
)


def create_notification_log(
    tenant_id: int,
    success: bool,
    amount_deducted: Decimal,
    recipient_summary: str,
    template_summary: str = "",
    failure_reason: str = "",
    message_body: str = "",
    message_mode: str = "",
    sqs_message_id: str = "",
    provider_message_id: str = "",
    notification_type: str = "",
    source_tenant_id: int | None = None,
    target_type: str = "",
    target_id: int | str | None = None,
    target_name: str = "",
) -> bool:
    """
    NotificationLog 1건 생성. Worker에서 직접 ORM 접근 대신 이 함수만 사용.

    Returns:
        True: 정상 생성됨
        False: sqs_message_id 기준 중복 (이미 성공 기록 존재) → 생성 안 함
    """
    from apps.domains.messaging.models import NotificationLog
    from apps.domains.messaging.security import sanitize_message_body_for_log

    # DB-level dedup: 동일 SQS 메시지에 대해 이미 성공 기록이 있으면 스킵
    if sqs_message_id and success:
        if NotificationLog.objects.filter(
            sqs_message_id=sqs_message_id, success=True
        ).exists():
            return False

    NotificationLog.objects.create(
        tenant_id=tenant_id,
        success=success,
        amount_deducted=amount_deducted,
        recipient_summary=recipient_summary[:500] if recipient_summary else "",
        template_summary=template_summary[:255] if template_summary else "",
        failure_reason=failure_reason[:500] if failure_reason else "",
        message_body=sanitize_message_body_for_log(
            message_body,
            notification_type=notification_type,
        )[:2000],
        message_mode=message_mode[:20] if message_mode else "",
        sqs_message_id=sqs_message_id[:128] if sqs_message_id else "",
        provider_message_id=provider_message_id[:128] if provider_message_id else "",
        notification_type=notification_type[:30] if notification_type else "",
        source_tenant_id=source_tenant_id,
        target_type=target_type[:30] if target_type else "",
        target_id=str(target_id)[:80] if target_id is not None else "",
        target_name=target_name[:80] if target_name else "",
    )
    return True


def list_account_notification_logs(
    *,
    source_tenant_id: int,
    target_ids: list[str],
    limit: int = 5,
) -> list[dict[str, object]]:
    from apps.domains.messaging.models import NotificationLog
    from apps.domains.messaging.policy import get_owner_tenant_id

    qs = (
        NotificationLog.objects
        .filter(
            tenant_id=get_owner_tenant_id(),
            source_tenant_id=source_tenant_id,
            target_type="account",
            target_id__in=target_ids,
            notification_type__in=ACCOUNT_NOTIFICATION_TYPES,
        )
        .order_by("-sent_at")[: max(1, min(int(limit), 20))]
    )
    return [
        {
            "id": log.id,
            "sent_at": log.sent_at,
            "success": log.success,
            "status": log.status or ("sent" if log.success else "failed"),
            "notification_type": log.notification_type or "",
            "recipient_summary": log.recipient_summary or "",
            "provider_message_id": log.provider_message_id or "",
            "failure_reason": log.failure_reason or "",
            "target_id": log.target_id or "",
            "target_name": log.target_name or "",
        }
        for log in qs
    ]


def claim_notification_slot(
    tenant_id: int,
    message_mode: str,
    business_idempotency_key: str,
    sqs_message_id: str = "",
    recipient_summary: str = "",
    source_tenant_id: int | None = None,
    target_type: str = "",
    target_id: int | str | None = None,
    target_name: str = "",
    stale_after_seconds: int = 300,
) -> tuple[bool, int | None]:
    """
    Atomic claim: insert a 'processing' row. If unique constraint fails, it's a duplicate.

    Returns:
        (True, log_id): Slot claimed successfully. Proceed to send.
        (False, None): Duplicate. Already claimed/sent by another worker.
    """
    from apps.domains.messaging.models import NotificationLog

    if not business_idempotency_key:
        # Legacy message without business key — skip claim, fall through to old path
        return True, None

    now = tz.now()
    try:
        with transaction.atomic():
            log = NotificationLog.objects.create(
                tenant_id=tenant_id,
                message_mode=message_mode[:20] if message_mode else "",
                business_idempotency_key=business_idempotency_key,
                status="processing",
                claimed_at=now,
                success=False,
                amount_deducted=Decimal("0"),
                recipient_summary=recipient_summary[:500] if recipient_summary else "",
                sqs_message_id=sqs_message_id[:128] if sqs_message_id else "",
                source_tenant_id=source_tenant_id,
                target_type=target_type[:30] if target_type else "",
                target_id=str(target_id)[:80] if target_id is not None else "",
                target_name=target_name[:80] if target_name else "",
            )
        return True, log.id
    except IntegrityError:
        existing = (
            NotificationLog.objects
            .filter(
                tenant_id=tenant_id,
                message_mode=message_mode[:20] if message_mode else "",
                business_idempotency_key=business_idempotency_key,
            )
            .only("id", "status", "sqs_message_id", "claimed_at")
            .first()
        )
        same_sqs_message = bool(
            existing
            and sqs_message_id
            and existing.sqs_message_id == sqs_message_id[:128]
        )
        if existing and existing.status == "processing" and same_sqs_message:
            stale_cutoff = now - timedelta(seconds=max(1, int(stale_after_seconds or 300)))
            if existing.claimed_at is None or existing.claimed_at <= stale_cutoff:
                updated = NotificationLog.objects.filter(id=existing.id, status="processing").update(
                    claimed_at=now,
                    success=False,
                    amount_deducted=Decimal("0"),
                    failure_reason="",
                    sqs_message_id=sqs_message_id[:128],
                    recipient_summary=recipient_summary[:500] if recipient_summary else "",
                    source_tenant_id=source_tenant_id,
                    target_type=target_type[:30] if target_type else "",
                    target_id=str(target_id)[:80] if target_id is not None else "",
                    target_name=target_name[:80] if target_name else "",
                )
                if updated == 1:
                    return True, existing.id
            return False, existing.id
        if existing and existing.status == "failed" and same_sqs_message:
            updated = NotificationLog.objects.filter(id=existing.id, status="failed").update(
                status="processing",
                claimed_at=now,
                success=False,
                amount_deducted=Decimal("0"),
                failure_reason="",
                sqs_message_id=sqs_message_id[:128],
                recipient_summary=recipient_summary[:500] if recipient_summary else "",
                source_tenant_id=source_tenant_id,
                target_type=target_type[:30] if target_type else "",
                target_id=str(target_id)[:80] if target_id is not None else "",
                target_name=target_name[:80] if target_name else "",
            )
            if updated == 1:
                return True, existing.id
        return False, None


def finalize_notification(
    log_id: int,
    *,
    success: bool,
    amount_deducted: Decimal = Decimal("0"),
    template_summary: str = "",
    failure_reason: str = "",
    message_body: str = "",
    provider_message_id: str = "",
    notification_type: str = "",
) -> None:
    """Update a claimed notification slot with final result."""
    from apps.domains.messaging.models import NotificationLog
    from apps.domains.messaging.security import sanitize_message_body_for_log

    NotificationLog.objects.filter(id=log_id).update(
        success=success,
        amount_deducted=amount_deducted,
        status="sent" if success else "failed",
        template_summary=template_summary[:255] if template_summary else "",
        failure_reason=failure_reason[:500] if failure_reason else "",
        provider_message_id=provider_message_id[:128] if provider_message_id else "",
        message_body=sanitize_message_body_for_log(
            message_body,
            notification_type=notification_type,
        )[:2000],
        notification_type=notification_type[:30] if notification_type else "",
    )
