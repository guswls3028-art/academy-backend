"""
신규 커뮤니티 trigger(qna_answered, counsel_answered) 일괄 provision.

기존 _auto_provision은 config가 0건일 때만 실행되므로, 이미 다른 trigger로
provision된 테넌트는 신규 trigger의 MessageTemplate/AutoSendConfig 행이
DB에 생성되지 않음 → 자동발송 화면에서 "(템플릿 없음)" + 토글 OFF 고착.

이 마이그레이션은 모든 테넌트에 신규 trigger 2종의 기본 템플릿과
AutoSendConfig를 생성하되 enabled=False로 두어 학원의 명시적 동의 없이는
발송되지 않도록 한다.
"""
from django.db import migrations


COMMUNITY_TRIGGERS = ("qna_answered", "counsel_answered")


def _community_definitions():
    """default_templates._TEMPLATE_DEFINITIONS에서 community 트리거 정의만 추출.

    마이그레이션은 historical apps 모델만 사용하므로 default_templates 모듈은
    런타임 코드에서 직접 import 가능 (모델 의존성 없음).
    """
    from apps.domains.messaging.default_templates import _TEMPLATE_DEFINITIONS
    return {t: _TEMPLATE_DEFINITIONS[t] for t in COMMUNITY_TRIGGERS if t in _TEMPLATE_DEFINITIONS}


def forward(apps, schema_editor):
    Tenant = apps.get_model("core", "Tenant")
    MessageTemplate = apps.get_model("messaging", "MessageTemplate")
    AutoSendConfig = apps.get_model("messaging", "AutoSendConfig")

    defs = _community_definitions()
    if not defs:
        return

    created_tpl = 0
    created_cfg = 0
    for tenant in Tenant.objects.all():
        academy_name = tenant.name or "학원"
        for trigger, d in defs.items():
            tpl_name = d["name"].replace("{academy_name}", academy_name)
            tpl, was_new = MessageTemplate.objects.get_or_create(
                tenant=tenant,
                name=tpl_name,
                defaults={
                    "category": d["category"],
                    "subject": d.get("subject", ""),
                    "body": d["body"],
                    "is_system": True,
                },
            )
            if was_new:
                created_tpl += 1
            elif not tpl.is_system:
                tpl.is_system = True
                tpl.save(update_fields=["is_system"])

            _, cfg_new = AutoSendConfig.objects.get_or_create(
                tenant=tenant,
                trigger=trigger,
                defaults={
                    "template": tpl,
                    "enabled": False,  # 학원 명시적 동의 후 ON
                    "message_mode": "alimtalk",
                    "minutes_before": d.get("minutes_before"),
                },
            )
            if cfg_new:
                created_cfg += 1

    if created_tpl or created_cfg:
        print(
            f"\n  → community provision: MessageTemplate {created_tpl}건, "
            f"AutoSendConfig {created_cfg}건 생성 (enabled=False)"
        )


def reverse(apps, schema_editor):
    """역마이그레이션: 신규 trigger의 AutoSendConfig + 시스템 템플릿 제거."""
    MessageTemplate = apps.get_model("messaging", "MessageTemplate")
    AutoSendConfig = apps.get_model("messaging", "AutoSendConfig")

    AutoSendConfig.objects.filter(trigger__in=COMMUNITY_TRIGGERS).delete()
    # 시스템 템플릿(is_system=True)만 제거 — 사용자가 편집한 템플릿은 보존
    MessageTemplate.objects.filter(
        category="community",
        is_system=True,
        name__contains="질문 답변 완료",
    ).delete()
    MessageTemplate.objects.filter(
        category="community",
        is_system=True,
        name__contains="상담 답변 등록",
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("messaging", "0022_alter_autosendconfig_trigger"),
    ]

    operations = [
        migrations.RunPython(forward, reverse),
    ]
