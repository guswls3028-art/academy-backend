from __future__ import annotations

from dataclasses import dataclass

from apps.domains.messaging.alimtalk_content_builders import get_solapi_template_id, get_template_type


@dataclass(frozen=True)
class EffectiveTemplateStatus:
    solapi_template_id: str
    solapi_status: str
    source: str
    template_type: str = ""

    @property
    def is_approved(self) -> bool:
        return bool(self.solapi_template_id and self.solapi_status == "APPROVED")


def prime_effective_owner_templates(configs) -> list:
    """Batch-load owner exact-trigger templates to avoid one query per config."""
    from apps.domains.messaging.models import AutoSendConfig
    from apps.domains.messaging.policy import get_owner_tenant_id

    config_list = list(configs)
    owner_id = int(get_owner_tenant_id())
    unresolved_triggers = {
        config.trigger
        for config in config_list
        if not (get_solapi_template_id(config.trigger) or "").strip()
        and int(getattr(config, "tenant_id", 0) or 0) != owner_id
    }
    owner_templates = {
        owner_config.trigger: owner_config.template
        for owner_config in AutoSendConfig.objects.select_related("template").filter(
            tenant_id=owner_id,
            trigger__in=unresolved_triggers,
        )
    }
    for config in config_list:
        if int(getattr(config, "tenant_id", 0) or 0) == owner_id:
            template = getattr(config, "template", None)
        else:
            template = owner_templates.get(config.trigger)
        config._effective_owner_exact_template = template
    return config_list


def resolve_effective_template_status(config) -> EffectiveTemplateStatus:
    """Resolve the public template actually used for an AutoSendConfig."""
    content_template = getattr(config, "template", None)
    if not content_template:
        return EffectiveTemplateStatus(
            solapi_template_id="",
            solapi_status="",
            source="content_template_missing",
        )
    if int(content_template.tenant_id) != int(config.tenant_id):
        return EffectiveTemplateStatus(
            solapi_template_id="",
            solapi_status="",
            source="content_template_tenant_mismatch",
        )
    unified_template_type = get_template_type(config.trigger) or ""
    unified_template_id = (get_solapi_template_id(config.trigger) or "").strip()
    if unified_template_id:
        return EffectiveTemplateStatus(
            solapi_template_id=unified_template_id,
            solapi_status="APPROVED",
            source="unified",
            template_type=unified_template_type,
        )
    if unified_template_type:
        return EffectiveTemplateStatus(
            solapi_template_id="",
            solapi_status="",
            source="unified_missing",
            template_type=unified_template_type,
        )

    # 비통합 트리거도 테넌트가 연결한 provider template을 사용하지 않는다.
    # 실행 경로(send_event_notification)와 동일하게 owner의 exact trigger만 본다.
    from apps.domains.messaging.models import AutoSendConfig
    from apps.domains.messaging.policy import get_owner_tenant_id

    if hasattr(config, "_effective_owner_exact_template"):
        template = config._effective_owner_exact_template
    else:
        owner_id = int(get_owner_tenant_id())
        if int(getattr(config, "tenant_id", 0) or 0) == owner_id:
            owner_config = config
        else:
            owner_config = (
                AutoSendConfig.objects.select_related("template")
                .filter(tenant_id=owner_id, trigger=config.trigger)
                .first()
            )
        template = getattr(owner_config, "template", None)
    if template and int(template.tenant_id) != int(get_owner_tenant_id()):
        return EffectiveTemplateStatus(
            solapi_template_id="",
            solapi_status="",
            source="owner_template_tenant_mismatch",
        )
    return EffectiveTemplateStatus(
        solapi_template_id=((getattr(template, "solapi_template_id", "") or "").strip()),
        solapi_status=(getattr(template, "solapi_status", "") or ""),
        source="owner_exact" if template else "missing",
    )
