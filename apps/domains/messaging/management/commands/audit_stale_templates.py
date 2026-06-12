"""
사용 중지된 트리거에 매핑된 MessageTemplate / AutoSendConfig 식별.

배경:
- exam_score_published 자동 발송 제거(2026-05-12) 등 정책 변경으로 자동 발화 트리거 변동.
- 옛날 trigger에 연결된 stale 템플릿/설정이 enabled=True로 남아 있어 의도치 않은
  발송 가능성. 본 명령은 식별만 수행(read-only) — disable/delete는 사용자 판단 후 수동.

사용:
  python manage.py audit_stale_templates
  python manage.py audit_stale_templates --tenant 1
  python manage.py audit_stale_templates --disable        # 식별된 stale/무효 config 일괄 disable
"""
from __future__ import annotations

from django.core.management.base import BaseCommand

from apps.domains.messaging.effective_templates import resolve_effective_template_status
from apps.domains.messaging.models import AutoSendConfig, MessageTemplate
from apps.domains.messaging.policy import (
    TRIGGER_POLICY,
    IMPLEMENTED_AUTO_TRIGGERS,
    get_trigger_implementation_status,
)


class Command(BaseCommand):
    help = "사용 중지된 트리거에 묶인 stale MessageTemplate/AutoSendConfig 식별."

    def add_arguments(self, parser):
        parser.add_argument("--tenant", type=int, default=None, help="특정 테넌트만 점검")
        parser.add_argument(
            "--disable",
            action="store_true",
            help="DISABLED/UNKNOWN/manual_only enabled=True config 일괄 disable",
        )

    def handle(self, *args, **opts):
        tenant_id = opts.get("tenant")
        do_disable = opts.get("disable", False)

        cfg_qs = AutoSendConfig.objects.select_related("tenant", "template").all()
        if tenant_id:
            cfg_qs = cfg_qs.filter(tenant_id=tenant_id)

        # 1) DISABLED 정책 + enabled=True (정책상 발송 차단인데 활성화됨)
        disabled_but_enabled = []
        # 2) 정책에 등록되지 않은 (unknown) trigger
        unknown_triggers = []
        # 3) implementation_status == manual_only 인데 enabled=True (자동 발화 코드 부재)
        manual_only_enabled = []
        # 4) 실제 발송 resolver 기준 Solapi template 미승인인데 enabled=True
        not_approved_enabled = []

        for cfg in cfg_qs:
            trig = cfg.trigger
            policy = TRIGGER_POLICY.get(trig, "UNKNOWN")
            impl = get_trigger_implementation_status(trig)

            if policy == "UNKNOWN":
                unknown_triggers.append(cfg)
                continue
            if policy == "DISABLED" and cfg.enabled:
                disabled_but_enabled.append(cfg)
                continue
            if cfg.enabled and impl == "manual_only":
                manual_only_enabled.append(cfg)
            if cfg.enabled and cfg.template:
                if not resolve_effective_template_status(cfg).is_approved:
                    not_approved_enabled.append(cfg)

        # MessageTemplate 점검: solapi_template_id 보유 + solapi_status != APPROVED + 사용 처 없음
        tpl_qs = MessageTemplate.objects.all()
        if tenant_id:
            tpl_qs = tpl_qs.filter(tenant_id=tenant_id)
        orphan_templates = []
        for tpl in tpl_qs:
            sid = (tpl.solapi_template_id or "").strip()
            status = tpl.solapi_status
            ref_count = AutoSendConfig.objects.filter(template=tpl, enabled=True).count()
            if sid and status != "APPROVED" and ref_count == 0:
                orphan_templates.append(tpl)

        # 보고
        self.stdout.write(self.style.MIGRATE_HEADING("=== 자동발송 트리거 점검 ==="))
        self.stdout.write(f"총 AutoSendConfig: {cfg_qs.count()}")
        self.stdout.write(f"등록된 트리거 정책: {len(TRIGGER_POLICY)}")
        self.stdout.write(f"코드 자동 발화 트리거: {len(IMPLEMENTED_AUTO_TRIGGERS)}")

        def report_section(title: str, items: list, fmt):
            self.stdout.write("")
            if not items:
                self.stdout.write(self.style.SUCCESS(f"[OK] {title}: 0건"))
                return
            self.stdout.write(self.style.WARNING(f"[!] {title}: {len(items)}건"))
            for it in items:
                self.stdout.write("  - " + fmt(it))

        report_section(
            "DISABLED 정책 + enabled=True (정책상 발송 차단인데 활성화)",
            disabled_but_enabled,
            lambda c: f"tenant={c.tenant_id} trigger={c.trigger} template_id={c.template_id} updated={c.updated_at:%Y-%m-%d}",
        )
        report_section(
            "정책 미등록 (UNKNOWN) trigger",
            unknown_triggers,
            lambda c: f"tenant={c.tenant_id} trigger={c.trigger} (정책 dict에 없음)",
        )
        report_section(
            "manual_only 인데 enabled=True (자동 발화 코드 없음 — 토글이 무효)",
            manual_only_enabled,
            lambda c: f"tenant={c.tenant_id} trigger={c.trigger} template_id={c.template_id}",
        )
        report_section(
            "enabled=True 이지만 실효 Solapi template 미승인",
            not_approved_enabled,
            lambda c: (
                f"tenant={c.tenant_id} trigger={c.trigger} "
                f"template={c.template.name if c.template else '?'} "
                f"linked_status={c.template.solapi_status if c.template else '?'} "
                f"effective_source={resolve_effective_template_status(c).source} "
                f"effective_status={resolve_effective_template_status(c).solapi_status}"
            ),
        )
        report_section(
            "Orphan MessageTemplate (solapi_template_id 보유 + status!=APPROVED + active config 없음)",
            orphan_templates,
            lambda t: f"tenant={t.tenant_id} id={t.id} name={t.name!r} solapi_id={t.solapi_template_id} status={t.solapi_status}",
        )

        if do_disable:
            self.stdout.write("")
            self.stdout.write(self.style.MIGRATE_HEADING("=== --disable 적용 ==="))
            cnt = 0
            for c in disabled_but_enabled:
                c.enabled = False
                c.save(update_fields=["enabled", "updated_at"])
                cnt += 1
            self.stdout.write(self.style.SUCCESS(f"DISABLED 정책 trigger {cnt}건 enabled=False 처리"))
            cnt2 = 0
            for c in unknown_triggers:
                if c.enabled:
                    c.enabled = False
                    c.save(update_fields=["enabled", "updated_at"])
                    cnt2 += 1
            self.stdout.write(self.style.SUCCESS(f"UNKNOWN trigger {cnt2}건 enabled=False 처리"))
            cnt3 = 0
            for c in manual_only_enabled:
                c.enabled = False
                c.save(update_fields=["enabled", "updated_at"])
                cnt3 += 1
            self.stdout.write(self.style.SUCCESS(f"manual_only trigger {cnt3}건 enabled=False 처리"))
        else:
            self.stdout.write("")
            self.stdout.write("실제 disable 적용은 --disable 옵션 필요. (read-only 보고)")
