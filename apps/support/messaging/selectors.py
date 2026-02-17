# apps/support/messaging/selectors.py

from typing import Optional

from apps.support.messaging.models import AutoSendConfig


def get_active_provider():
    return None


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
