# apps/support/messaging/selectors.py

from typing import Optional

from apps.support.messaging.models import AutoSendConfig


def get_auto_send_config(tenant_id: int, trigger: str) -> Optional[AutoSendConfig]:
    """테넌트·트리거별 자동발송 설정 조회."""
    return AutoSendConfig.objects.filter(
        tenant_id=tenant_id,
        trigger=trigger,
        enabled=True,
    ).select_related("template").first()


def get_all_auto_send_configs(tenant_id: int) -> dict[str, Optional[AutoSendConfig]]:
    """테넌트의 모든 자동발송 설정 조회. trigger -> config."""
    configs = AutoSendConfig.objects.filter(tenant_id=tenant_id).select_related("template")
    return {c.trigger: c for c in configs}


def resolve_freeform_template(tenant_id: int):
    """
    알림톡 자유양식 APPROVED 템플릿 resolve.
    1) 현재 테넌트의 APPROVED 템플릿 (#{공지내용} 또는 #{내용})
    2) 없으면 오너 테넌트의 APPROVED 템플릿으로 fallback
    Note: #{선생님메모}는 Solapi 전용 변수명으로 DB body에는 존재하지 않음.
    Returns: MessageTemplate | None
    """
    from apps.support.messaging.models import MessageTemplate
    from apps.support.messaging.policy import get_owner_tenant_id

    for tid in [tenant_id, get_owner_tenant_id()]:
        freeform = MessageTemplate.objects.filter(
            tenant_id=tid, solapi_status="APPROVED", body__contains="#{공지내용}",
        ).first()
        if not freeform:
            freeform = MessageTemplate.objects.filter(
                tenant_id=tid, solapi_status="APPROVED", body__contains="#{내용}",
            ).first()
        if freeform:
            return freeform
    return None


def resolve_category_template(tenant_id: int, extra_vars: dict = None):
    """
    카테고리별 승인 템플릿 fallback.
    extra_vars에 시험성적이 있으면 #{시험성적} 변수를 가진 APPROVED grades 템플릿 반환.
    """
    from apps.support.messaging.models import MessageTemplate
    from apps.support.messaging.policy import get_owner_tenant_id

    if not extra_vars or "시험성적" not in extra_vars:
        return None
    for tid in [tenant_id, get_owner_tenant_id()]:
        tpl = MessageTemplate.objects.filter(
            tenant_id=tid, solapi_status="APPROVED",
            body__contains="#{시험성적}",
        ).first()
        if tpl:
            return tpl
    return None


def has_any_approved_template(tenant_id: int) -> bool:
    """테넌트 또는 오너 테넌트에 APPROVED 알림톡 템플릿이 있는지 확인."""
    from apps.support.messaging.models import MessageTemplate
    from apps.support.messaging.policy import get_owner_tenant_id

    if MessageTemplate.objects.filter(tenant_id=tenant_id, solapi_status="APPROVED").exists():
        return True
    owner_id = get_owner_tenant_id()
    if tenant_id != owner_id:
        return MessageTemplate.objects.filter(tenant_id=owner_id, solapi_status="APPROVED").exists()
    return False
