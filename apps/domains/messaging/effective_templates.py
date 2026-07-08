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


def resolve_effective_template_status(config) -> EffectiveTemplateStatus:
    """Resolve the Solapi template actually used for an AutoSendConfig."""
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

    template = getattr(config, "template", None)
    return EffectiveTemplateStatus(
        solapi_template_id=((getattr(template, "solapi_template_id", "") or "").strip()),
        solapi_status=(getattr(template, "solapi_status", "") or ""),
        source="tenant_template" if template else "missing",
    )
