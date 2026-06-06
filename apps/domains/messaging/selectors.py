# apps/support/messaging/selectors.py

from typing import Optional

from apps.domains.messaging.models import AutoSendConfig


def get_auto_send_config(tenant_id: int, trigger: str) -> Optional[AutoSendConfig]:
    """테넌트·트리거별 자동발송 설정 조회.
    enabled 여부와 무관하게 config 행 자체를 반환.
    → 호출자가 config.enabled를 체크하여 비활성이면 스킵.
    → config 행이 존재하면(비활성 포함) 호출자가 그 상태를 그대로 존중한다.
    """
    return AutoSendConfig.objects.filter(
        tenant_id=tenant_id,
        trigger=trigger,
    ).select_related("template").first()


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
    ).first()
    if not freeform:
        freeform = MessageTemplate.objects.filter(
            tenant_id=owner_id, solapi_status="APPROVED", body__contains="#{내용}",
        ).first()
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
    ).first()


def has_any_approved_template(tenant_id: int) -> bool:
    """공용 오너 테넌트에 APPROVED 알림톡 템플릿이 있는지 확인."""
    from apps.domains.messaging.models import MessageTemplate
    from apps.domains.messaging.policy import get_owner_tenant_id

    return MessageTemplate.objects.filter(
        tenant_id=get_owner_tenant_id(),
        solapi_status="APPROVED",
    ).exists()
