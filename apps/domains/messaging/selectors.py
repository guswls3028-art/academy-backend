# apps/support/messaging/selectors.py

from datetime import timedelta
from typing import Optional

from django.db.models import Q
from django.utils import timezone

from apps.domains.messaging.models import AutoSendConfig, NotificationLog
from apps.domains.messaging.scheduled import HOURLY_SEND_LIMIT


def notification_logs_for_business_tenant(tenant):
    """Return logs owned by the tenant that caused the business event.

    Provider delivery is normalized to the common owner tenant.  For those
    proxy sends ``source_tenant`` is the sole business owner; a physical owner
    tenant must not gain visibility merely because it supplied the channel.
    """
    tenant_id = int(getattr(tenant, "id", tenant))
    return NotificationLog.objects.filter(
        Q(source_tenant_id=tenant_id)
        | Q(source_tenant_id__isnull=True, tenant_id=tenant_id)
    )


def get_hourly_notification_usage(tenant, *, now=None) -> int:
    """Count dispatch attempts plus legacy direct-send logs in the rolling hour."""
    from apps.domains.messaging.models import ScheduledNotification

    cutoff = (now or timezone.now()) - timedelta(hours=1)
    tenant_id = int(getattr(tenant, "id", tenant))
    outbox_count = ScheduledNotification.objects.filter(
        tenant_id=tenant_id,
        last_attempt_at__gte=cutoff,
    ).count()
    outbox_keys = list(
        ScheduledNotification.objects.filter(
            tenant_id=tenant_id,
            last_attempt_at__gte=cutoff,
        )
        .exclude(business_idempotency_key="")
        .values_list("business_idempotency_key", flat=True)
    )
    legacy_log_count = (
        notification_logs_for_business_tenant(tenant)
        .filter(sent_at__gte=cutoff)
        .exclude(business_idempotency_key__in=outbox_keys)
        .count()
    )
    return outbox_count + legacy_log_count


def get_auto_send_config(tenant_id: int, trigger: str) -> Optional[AutoSendConfig]:
    """테넌트·트리거별 자동발송 설정 조회.
    enabled 여부와 무관하게 config 행 자체를 반환.
    → 호출자가 config.enabled를 체크하여 비활성이면 스킵.
    → config 행이 존재하면(비활성 포함) 호출자가 그 상태를 그대로 존중한다.
    """
    return AutoSendConfig.objects.filter(
        tenant_id=tenant_id,
        trigger=trigger,
    ).select_related("template").order_by("id").first()


def get_all_auto_send_configs(tenant_id: int) -> dict[str, Optional[AutoSendConfig]]:
    """테넌트의 모든 자동발송 설정 조회. trigger -> config."""
    configs = AutoSendConfig.objects.filter(tenant_id=tenant_id).select_related("template")
    return {c.trigger: c for c in configs}


def resolve_freeform_template(tenant_id: int):
    """
    알림톡 자유양식 APPROVED 템플릿 resolve.
    SSOT: 공용 오너 테넌트의 APPROVED 템플릿만 사용한다.
    Note: #{선생님메모}는 Solapi 전용 변수명으로 DB body에는 존재하지 않음.
    Returns: MessageTemplate | None
    """
    from apps.domains.messaging.models import MessageTemplate
    from apps.domains.messaging.policy import get_owner_tenant_id

    owner_id = get_owner_tenant_id()
    freeform = MessageTemplate.objects.filter(
        tenant_id=owner_id, solapi_status="APPROVED", body__contains="#{공지내용}",
    ).order_by("-updated_at", "-id").first()
    if not freeform:
        freeform = MessageTemplate.objects.filter(
            tenant_id=owner_id, solapi_status="APPROVED", body__contains="#{내용}",
        ).order_by("-updated_at", "-id").first()
    return freeform


def resolve_category_template(tenant_id: int, extra_vars: dict = None):
    """
    카테고리별 승인 템플릿 resolve.
    SSOT: 공용 오너 테넌트의 APPROVED 템플릿만 사용한다.
    extra_vars에 시험성적이 있으면 #{시험성적} 변수를 가진 APPROVED grades 템플릿 반환.
    """
    from apps.domains.messaging.models import MessageTemplate
    from apps.domains.messaging.policy import get_owner_tenant_id

    if not extra_vars or "시험성적" not in extra_vars:
        return None
    owner_id = get_owner_tenant_id()
    return MessageTemplate.objects.filter(
        tenant_id=owner_id, solapi_status="APPROVED",
        body__contains="#{시험성적}",
    ).order_by("-updated_at", "-id").first()


def has_any_approved_template(tenant_id: int) -> bool:
    """공용 오너 테넌트에 APPROVED 알림톡 템플릿이 있는지 확인."""
    from apps.domains.messaging.models import MessageTemplate
    from apps.domains.messaging.policy import get_owner_tenant_id

    return MessageTemplate.objects.filter(
        tenant_id=get_owner_tenant_id(),
        solapi_status="APPROVED",
    ).exists()
