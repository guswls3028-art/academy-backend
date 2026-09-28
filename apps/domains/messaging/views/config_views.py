# apps/support/messaging/views/config_views.py
"""
자동발송 설정 뷰 — AutoSendConfig, 기본 템플릿 프로비저닝
"""

from django.db import transaction
from rest_framework import status
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated

from apps.core.parsing import parse_bool
from apps.core.permissions import TenantResolvedAndStaff
from apps.core.models import Tenant
from apps.domains.messaging.default_templates import get_default_templates
from apps.domains.messaging.effective_templates import (
    prime_effective_owner_templates,
    resolve_effective_template_status,
)
from apps.domains.messaging.models import (
    MessageTemplate, AutoSendConfig, DefaultTemplateSuppression,
)
from apps.domains.messaging.permissions import can_manage_messaging_settings
from apps.domains.messaging.policy import is_auto_send_enabled_by_default
from apps.domains.messaging.serializers import AutoSendConfigSerializer


def _default_enabled_for_trigger(trigger: str) -> bool:
    return is_auto_send_enabled_by_default(trigger)


def _can_manage_auto_send(request, tenant) -> bool:
    return can_manage_messaging_settings(request, tenant)


def _auto_send_write_forbidden_response():
    return Response(
        {"detail": "자동발송 설정은 대표 또는 관리자만 변경할 수 있습니다."},
        status=status.HTTP_403_FORBIDDEN,
    )


class AutoSendConfigView(APIView):
    """
    GET: 테넌트의 모든 자동발송 설정 목록 (트리거별)
    PATCH: 트리거별 설정 수정. Body: { "configs": [ { "trigger": "...", "template_id": null|int, "enabled": bool, "message_mode": "alimtalk" }, ... ] }
    """
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    def get(self, request):
        tenant = request.tenant
        triggers = [c[0] for c in AutoSendConfig.Trigger.choices]
        configs = AutoSendConfig.objects.filter(tenant=tenant).select_related("template").defer("delay_mode", "delay_value")

        from apps.domains.messaging.alimtalk_content_builders import get_template_type
        from apps.domains.messaging.policy import get_trigger_policy, get_trigger_implementation_status

        configs = prime_effective_owner_templates(configs)
        by_trigger = {c.trigger: c for c in configs}

        result = []
        for trigger in triggers:
            c = by_trigger.get(trigger)
            policy_mode = get_trigger_policy(trigger)
            impl_status = get_trigger_implementation_status(trigger)
            if c:
                data = AutoSendConfigSerializer(c).data
                data["policy_mode"] = policy_mode
                data["implementation_status"] = impl_status
                result.append(data)
            else:
                result.append({
                    "id": None,
                    "trigger": trigger,
                    "template": None,
                    "template_name": "",
                    "template_subject": "",
                    "template_body": "",
                    "template_solapi_status": "",
                    "effective_solapi_template_id": "",
                    "effective_template_solapi_status": "",
                    "effective_template_source": "missing",
                    "effective_template_is_approved": False,
                    "effective_template_type": get_template_type(trigger) or "",
                    "enabled": False,
                    "message_mode": "alimtalk",
                    "minutes_before": None,
                    "created_at": None,
                    "updated_at": None,
                    "policy_mode": policy_mode,
                    "implementation_status": impl_status,
                })
        return Response(result)

    @transaction.atomic
    def patch(self, request):
        tenant = request.tenant
        if not _can_manage_auto_send(request, tenant):
            return _auto_send_write_forbidden_response()

        configs_data = request.data.get("configs") or []
        if not isinstance(configs_data, list):
            return Response(
                {"detail": "configs는 배열이어야 합니다."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        from apps.domains.messaging.policy import get_trigger_implementation_status

        def reject(payload):
            transaction.set_rollback(True)
            return Response(payload, status=status.HTTP_400_BAD_REQUEST)

        rejected_triggers = []
        for item in configs_data:
            trigger = (item.get("trigger") or "").strip()
            if not trigger or trigger not in dict(AutoSendConfig.Trigger.choices):
                continue
            should_update_enabled = "enabled" in item
            requested_enabled = (
                parse_bool(item.get("enabled"), field_name="enabled")
                if should_update_enabled
                else None
            )
            # 미구현/DISABLED 트리거는 자동 발송 ON 차단 — 운영자 혼란 방지
            enabled = requested_enabled
            if enabled:
                impl_status = get_trigger_implementation_status(trigger)
                if impl_status != "implemented":
                    enabled = False
                    rejected_triggers.append({"trigger": trigger, "reason": impl_status})
            minutes_before = item.get("minutes_before")
            if minutes_before is not None:
                try:
                    minutes_before = int(minutes_before) if minutes_before != "" else None
                except (TypeError, ValueError):
                    return reject({"minutes_before": "발송 시점 값은 숫자여야 합니다."})
                if minutes_before is not None and minutes_before < 0:
                    return reject({"minutes_before": "발송 시점 값은 0 이상이어야 합니다."})

            config, _ = AutoSendConfig.objects.select_for_update().get_or_create(
                tenant=tenant,
                trigger=trigger,
                defaults={"enabled": False, "message_mode": "alimtalk"},
            )
            if "template_id" in item:
                template_id = item.get("template_id")
                if template_id:
                    try:
                        template_pk = int(template_id)
                    except (TypeError, ValueError):
                        return reject({"template_id": "템플릿 ID는 숫자여야 합니다."})
                    template = MessageTemplate.objects.filter(tenant=tenant, pk=template_pk).first()
                    if template is None:
                        return reject({"template_id": "해당 템플릿을 찾을 수 없습니다."})
                    config.template = template
                else:
                    config.template = None
            if should_update_enabled:
                config.enabled = enabled
            if "message_mode" in item:
                message_mode = (item.get("message_mode") or "alimtalk").strip().lower()
                if message_mode != "alimtalk":
                    return reject({"message_mode": "공용 알림톡만 설정할 수 있습니다."})
                config.message_mode = message_mode
            if "minutes_before" in item:
                config.minutes_before = minutes_before

            # show_actual_time — 클리닉 출석/결석 알림 시간 표시 모드
            if hasattr(config, "show_actual_time"):
                sat = item.get("show_actual_time")
                if sat is not None:
                    config.show_actual_time = parse_bool(sat, field_name="show_actual_time")

            # delay_mode / delay_value — 마이그레이션 전에도 안전 (hasattr 체크)
            if hasattr(config, "delay_mode"):
                current_delay_mode = (getattr(config, "delay_mode", "") or "immediate").strip().lower()
                requested_delay_mode = item.get("delay_mode")
                delay_mode = (
                    (requested_delay_mode or "").strip().lower()
                    if requested_delay_mode is not None
                    else current_delay_mode
                )
                if delay_mode not in ("immediate", "delay_minutes", "scheduled_hour"):
                    return reject({"delay_mode": "지원하지 않는 발송 지연 방식입니다."})
                delay_value = item.get("delay_value")
                parsed_delay_value = None
                if delay_value is not None:
                    try:
                        parsed_delay_value = int(delay_value) if delay_value != "" else None
                    except (TypeError, ValueError):
                        return reject({"delay_value": "발송 지연 값은 숫자여야 합니다."})
                    if parsed_delay_value is not None and parsed_delay_value < 0:
                        return reject({"delay_value": "발송 지연 값은 0 이상이어야 합니다."})
                    if delay_mode == "scheduled_hour" and parsed_delay_value is not None and not 0 <= parsed_delay_value <= 23:
                        return reject({"delay_value": "지정 시각은 0~23 사이여야 합니다."})
                elif requested_delay_mode is not None and delay_mode != "immediate":
                    if delay_mode != current_delay_mode:
                        return reject({"delay_value": "발송 지연 방식을 바꿀 때는 값을 함께 지정해야 합니다."})
                    current_delay_value = getattr(config, "delay_value", None)
                    if current_delay_value is None:
                        return reject({"delay_value": "발송 지연 값이 필요합니다."})
                    if delay_mode == "scheduled_hour" and not 0 <= int(current_delay_value) <= 23:
                        return reject({"delay_value": "지정 시각은 0~23 사이여야 합니다."})

                config.delay_mode = delay_mode
                if delay_value is not None:
                    if parsed_delay_value is None or delay_mode == "immediate":
                        config.delay_value = None
                    elif delay_mode == "scheduled_hour":
                        config.delay_value = parsed_delay_value
                    else:
                        config.delay_value = max(0, parsed_delay_value)
                elif requested_delay_mode is not None and delay_mode == "immediate":
                    config.delay_value = None

            if config.enabled:
                effective_template = resolve_effective_template_status(config)
                if not effective_template.is_approved:
                    return reject({
                        "template_id": "자동발송을 켜려면 승인된 알림톡 템플릿이 필요합니다.",
                        "trigger": trigger,
                        "effective_template_source": effective_template.source,
                        "effective_solapi_template_id": effective_template.solapi_template_id,
                        "effective_solapi_status": effective_template.solapi_status,
                    })

            config.save()

        if rejected_triggers:
            import logging
            _log = logging.getLogger(__name__)
            _log.info(
                "AutoSendConfig PATCH rejected enable for unimplemented triggers tenant=%s rejected=%s",
                tenant.id, rejected_triggers,
            )
        configs = prime_effective_owner_templates(
            AutoSendConfig.objects.filter(tenant=tenant)
            .select_related("template")
            .defer("delay_mode", "delay_value")
        )
        from apps.domains.messaging.policy import get_trigger_policy, get_trigger_implementation_status
        result = []
        for c in configs:
            data = AutoSendConfigSerializer(c).data
            data["policy_mode"] = get_trigger_policy(c.trigger)
            data["implementation_status"] = get_trigger_implementation_status(c.trigger)
            result.append(data)
        return Response(result)


class ProvisionDefaultTemplatesView(APIView):
    """GET: 복원 가능 기본 문구. POST: 미설정 트리거 초기화/선택 복원."""
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    @staticmethod
    def _suppressed_defaults(tenant, definitions):
        academy_name = tenant.name or "학원"
        saved = set(DefaultTemplateSuppression.objects.filter(
            tenant=tenant,
        ).values_list("default_key", flat=True))
        templates = list(MessageTemplate.objects.filter(tenant=tenant).only("name"))
        configs = list(AutoSendConfig.objects.filter(tenant=tenant).only("trigger", "template_id"))
        names = {template.name for template in templates}
        present = {key for key, definition in definitions.items() if definition["name"] in names}
        present.update(key for key, old_name in {
            "freeform_general": f"[{academy_name}] 학원 안내",
            "freeform_payment": f"[{academy_name}] 결제 안내",
            "freeform_clinic": f"[{academy_name}] 클리닉 안내",
        }.items() if old_name in names)
        configured = {config.trigger for config in configs}
        existing_setup = bool(templates or configs)
        return [
            {"key": key, "name": definition["name"], "category": definition["category"]}
            for key, definition in definitions.items()
            if key in saved or (
                key not in present and (
                    key in configured or (key.startswith("freeform_") and existing_setup)
                )
            )
        ]

    def get(self, request):
        tenant = request.tenant
        if not _can_manage_auto_send(request, tenant):
            return _auto_send_write_forbidden_response()
        definitions = get_default_templates(tenant.name or "학원")
        return Response({"suppressed_defaults": self._suppressed_defaults(tenant, definitions)})

    @transaction.atomic
    def post(self, request):
        tenant = request.tenant
        if not _can_manage_auto_send(request, tenant):
            return _auto_send_write_forbidden_response()
        restore_keys = request.data.get("restore_keys", [])
        templates = get_default_templates(tenant.name or "학원")
        if (not isinstance(restore_keys, list) or
                any(not isinstance(key, str) or key not in templates for key in restore_keys) or
                len(set(restore_keys)) != len(restore_keys)):
            return Response(
                {"restore_keys": "복원할 기본 문구 키를 중복 없이 선택해 주세요."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        Tenant.objects.select_for_update().get(pk=tenant.pk)
        restore_keys = set(restore_keys)
        if restore_keys:
            DefaultTemplateSuppression.objects.filter(
                tenant=tenant, default_key__in=restore_keys,
            ).delete()
        suppressed = set(DefaultTemplateSuppression.objects.filter(
            tenant=tenant,
        ).values_list("default_key", flat=True))
        existing_configs = {
            c.trigger: c
            for c in AutoSendConfig.objects.filter(tenant=tenant).select_related("template")
            .defer("delay_mode", "delay_value")
        }
        existing_templates = {
            template.name: template
            for template in MessageTemplate.objects.filter(tenant=tenant).order_by("updated_at")
        }
        existing_setup = bool(existing_configs or existing_templates)
        created_templates = 0
        created_configs = 0
        reset_templates = 0
        linked = 0

        academy_name = tenant.name or "학원"
        legacy_freeform_names = {
            "freeform_general": f"[{academy_name}] 학원 안내",
            "freeform_payment": f"[{academy_name}] 결제 안내",
            "freeform_clinic": f"[{academy_name}] 클리닉 안내",
        }
        valid_triggers = {choice[0] for choice in AutoSendConfig.Trigger.choices}

        for trigger, defaults in templates.items():
            if trigger in suppressed:
                continue
            tpl_name = defaults["name"]
            existing = existing_configs.get(trigger) if trigger in valid_triggers else None
            # An existing trigger's chosen content (or explicit empty selection)
            # is authoritative. Provision never silently rewires that choice.
            if existing and trigger not in restore_keys:
                continue
            tpl = existing_templates.get(tpl_name) or existing_templates.get(
                legacy_freeform_names.get(trigger, "")
            )
            # Existing tenants may have removed an older freeform default before
            # deletion tracking existed. Keep that absence until a named restore.
            if (tpl is None and trigger.startswith("freeform_")
                    and existing_setup and trigger not in restore_keys):
                continue
            if tpl is None:
                tpl = MessageTemplate.objects.create(
                    tenant=tenant,
                    name=tpl_name,
                    category=defaults["category"],
                    subject=defaults.get("subject", ""),
                    body=defaults["body"],
                    is_system=True,
                )
                existing_templates[tpl_name] = tpl
                created_templates += 1

            if trigger not in valid_triggers:
                continue
            if existing:
                if not existing.template_id:
                    existing.template = tpl
                    existing.save(update_fields=["template", "updated_at"])
                    linked += 1
            else:
                AutoSendConfig.objects.create(
                    tenant=tenant,
                    trigger=trigger,
                    template=tpl,
                    enabled=_default_enabled_for_trigger(trigger),
                    message_mode="alimtalk",
                    minutes_before=defaults.get("minutes_before"),
                )
                created_configs += 1

        total_configs = AutoSendConfig.objects.filter(tenant=tenant).count()

        submitted_reviews = 0
        review_errors = []

        return Response({
            "created_templates": created_templates,
            "created_configs": created_configs,
            "reset_templates": reset_templates,
            "linked": linked,
            "total_templates": MessageTemplate.objects.filter(tenant=tenant).count(),
            "total_configs": total_configs,
            "submitted_reviews": submitted_reviews,
            "review_errors": review_errors,
            "review_note": "",
            "suppressed_defaults": self._suppressed_defaults(tenant, templates),
        }, status=status.HTTP_200_OK)
