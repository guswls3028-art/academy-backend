# apps/support/messaging/views/config_views.py
"""
자동발송 설정 뷰 — AutoSendConfig, 기본 템플릿 프로비저닝
"""

from rest_framework import status
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated

from apps.core.permissions import TenantResolvedAndStaff
from apps.domains.messaging.models import MessageTemplate, AutoSendConfig
from apps.domains.messaging.solapi_template_client import create_kakao_template
from apps.domains.messaging.serializers import AutoSendConfigSerializer


class AutoSendConfigView(APIView):
    """
    GET: 테넌트의 모든 자동발송 설정 목록 (트리거별)
    PATCH: 트리거별 설정 수정. Body: { "configs": [ { "trigger": "...", "template_id": null|int, "enabled": bool, "message_mode": "sms"|"alimtalk" }, ... ] }
    """
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    def get(self, request):
        tenant = request.tenant
        triggers = [c[0] for c in AutoSendConfig.Trigger.choices]
        configs = AutoSendConfig.objects.filter(tenant=tenant).select_related("template").defer("delay_mode", "delay_value")

        # ── 자동 프로비저닝: config가 하나도 없으면 기본 템플릿 + config 자동 생성 ──
        if not configs.exists():
            self._auto_provision(tenant)
            configs = AutoSendConfig.objects.filter(tenant=tenant).select_related("template").defer("delay_mode", "delay_value")

        from apps.domains.messaging.policy import get_trigger_policy, get_trigger_implementation_status

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
                    "enabled": False,
                    "message_mode": "alimtalk",
                    "minutes_before": None,
                    "created_at": None,
                    "updated_at": None,
                    "policy_mode": policy_mode,
                    "implementation_status": impl_status,
                })
        return Response(result)

    @staticmethod
    def _auto_provision(tenant):
        """기본 템플릿 + AutoSendConfig 자동 생성 (첫 접근 시 1회)"""
        from ..default_templates import get_default_templates
        import logging
        logger = logging.getLogger(__name__)

        templates = get_default_templates(tenant.name or "학원")
        valid_triggers = {c[0] for c in AutoSendConfig.Trigger.choices}
        for trigger, defaults in templates.items():
            tpl_name = defaults["name"]
            tpl, created = MessageTemplate.objects.get_or_create(
                tenant=tenant,
                name=tpl_name,
                defaults={
                    "category": defaults["category"],
                    "subject": defaults.get("subject", ""),
                    "body": defaults["body"],
                    "is_system": True,
                },
            )
            # 기존 시스템 템플릿이 is_system=False이면 교정
            if not created and not tpl.is_system:
                tpl.is_system = True
                tpl.save(update_fields=["is_system"])
            # 자유양식 템플릿 등 유효한 트리거가 아니면 AutoSendConfig 생성 스킵
            if trigger not in valid_triggers:
                continue
            AutoSendConfig.objects.get_or_create(
                tenant=tenant,
                trigger=trigger,
                defaults={
                    "template": tpl,
                    "enabled": True,
                    "message_mode": "alimtalk",
                    "minutes_before": defaults.get("minutes_before"),
                },
            )
        logger.info("Auto-provisioned default templates for tenant %s", tenant.id)

    def patch(self, request):
        tenant = request.tenant
        configs_data = request.data.get("configs") or []
        if not isinstance(configs_data, list):
            return Response(
                {"detail": "configs는 배열이어야 합니다."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        from apps.domains.messaging.policy import get_trigger_implementation_status

        rejected_triggers = []
        for item in configs_data:
            trigger = (item.get("trigger") or "").strip()
            if not trigger or trigger not in dict(AutoSendConfig.Trigger.choices):
                continue
            template_id = item.get("template_id")
            enabled = item.get("enabled", False)
            # 미구현/DISABLED 트리거는 자동 발송 ON 차단 — 운영자 혼란 방지
            if enabled:
                impl_status = get_trigger_implementation_status(trigger)
                if impl_status != "implemented":
                    enabled = False
                    rejected_triggers.append({"trigger": trigger, "reason": impl_status})
            message_mode = (item.get("message_mode") or "alimtalk").strip().lower()
            if message_mode not in ("sms", "alimtalk", "both"):
                message_mode = "alimtalk"
            minutes_before = item.get("minutes_before")
            if minutes_before is not None:
                try:
                    minutes_before = max(0, int(minutes_before)) if minutes_before != "" else None
                except (TypeError, ValueError):
                    minutes_before = None

            config, _ = AutoSendConfig.objects.get_or_create(
                tenant=tenant,
                trigger=trigger,
                defaults={"enabled": False, "message_mode": "alimtalk"},
            )
            if template_id:
                t = MessageTemplate.objects.filter(
                    tenant=tenant, pk=int(template_id)
                ).first()
                config.template = t
            else:
                config.template = None
            config.enabled = enabled
            config.message_mode = message_mode
            config.minutes_before = minutes_before

            # show_actual_time — 클리닉 출석/결석 알림 시간 표시 모드
            if hasattr(config, "show_actual_time"):
                sat = item.get("show_actual_time")
                if sat is not None:
                    config.show_actual_time = bool(sat)

            # delay_mode / delay_value — 마이그레이션 전에도 안전 (hasattr 체크)
            if hasattr(config, "delay_mode"):
                delay_mode = (item.get("delay_mode") or "").strip().lower()
                if delay_mode in ("immediate", "delay_minutes", "scheduled_hour"):
                    config.delay_mode = delay_mode
                delay_value = item.get("delay_value")
                if delay_value is not None:
                    try:
                        config.delay_value = max(0, int(delay_value)) if delay_value != "" else None
                    except (TypeError, ValueError):
                        config.delay_value = None
                elif delay_mode == "immediate":
                    config.delay_value = None

            config.save()

        if rejected_triggers:
            import logging
            _log = logging.getLogger(__name__)
            _log.info(
                "AutoSendConfig PATCH rejected enable for unimplemented triggers tenant=%s rejected=%s",
                tenant.id, rejected_triggers,
            )
        configs = AutoSendConfig.objects.filter(tenant=tenant).select_related("template").defer("delay_mode", "delay_value")
        from apps.domains.messaging.policy import get_trigger_policy, get_trigger_implementation_status
        result = []
        for c in configs:
            data = AutoSendConfigSerializer(c).data
            data["policy_mode"] = get_trigger_policy(c.trigger)
            data["implementation_status"] = get_trigger_implementation_status(c.trigger)
            result.append(data)
        return Response(result)


class ProvisionDefaultTemplatesView(APIView):
    """POST: 기본 템플릿 + 자동발송 config 일괄 생성/리셋.
    - 기존 기본 템플릿(이름이 DEFAULT_TEMPLATES와 동일)은 최신 기본값으로 리셋
    - 사용자가 새로 만든 템플릿은 그대로 유지
    """
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    def post(self, request):
        from ..default_templates import get_default_templates

        tenant = request.tenant
        templates = get_default_templates(tenant.name or "학원")
        existing_configs = {
            c.trigger: c
            for c in AutoSendConfig.objects.filter(tenant=tenant).select_related("template").defer("delay_mode", "delay_value")
        }
        default_names = {d["name"] for d in templates.values()}
        created_templates = 0
        created_configs = 0
        reset_templates = 0
        linked = 0

        # 자유양식 템플릿 이름 변경 마이그레이션 (구 이름 → 신 이름)
        academy_name = tenant.name or "학원"
        _freeform_name_migrations = {
            f"[{academy_name}] 학원 안내": f"[{academy_name}] 공지사항 안내",
            f"[{academy_name}] 결제 안내": f"[{academy_name}] 수납 안내",
            f"[{academy_name}] 클리닉 안내": f"[{academy_name}] 보충수업 안내",
        }
        _old_to_new = {v: k for k, v in _freeform_name_migrations.items()}  # new → old

        for trigger, defaults in templates.items():
            tpl_name = defaults["name"]
            tpl_category = defaults["category"]
            tpl_subject = defaults.get("subject", "")
            tpl_body = defaults["body"]

            existing_tpl = MessageTemplate.objects.filter(
                tenant=tenant, name=tpl_name,
            ).first()

            # 이름 변경된 자유양식: 구 이름으로도 검색하여 rename
            if not existing_tpl and tpl_name in _old_to_new:
                old_name = _old_to_new[tpl_name]
                existing_tpl = MessageTemplate.objects.filter(
                    tenant=tenant, name=old_name,
                ).first()
                if existing_tpl:
                    existing_tpl.name = tpl_name
                    existing_tpl.save(update_fields=["name", "updated_at"])

            if existing_tpl:
                # 기존 시스템 템플릿이 is_system=False이면 교정
                if not existing_tpl.is_system:
                    existing_tpl.is_system = True
                    existing_tpl.save(update_fields=["is_system"])
                # 기본 템플릿이면 본문·제목·카테고리를 최신 기본값으로 리셋
                changed = False
                if existing_tpl.category != tpl_category:
                    existing_tpl.category = tpl_category
                    changed = True
                if existing_tpl.subject != tpl_subject:
                    existing_tpl.subject = tpl_subject
                    changed = True
                if existing_tpl.body != tpl_body:
                    existing_tpl.body = tpl_body
                    changed = True
                if changed:
                    update_fields = ["category", "subject", "body", "updated_at"]
                    # 자유양식 템플릿의 본문이 변경되면 솔라피 연동 상태 초기화
                    # (구 본문으로 검수 중/승인된 템플릿 ID가 새 본문과 불일치 → 3034 에러 방지)
                    if trigger.startswith("freeform_") and existing_tpl.solapi_template_id:
                        existing_tpl.solapi_template_id = ""
                        existing_tpl.solapi_status = ""
                        update_fields.extend(["solapi_template_id", "solapi_status"])
                    existing_tpl.save(update_fields=update_fields)
                    reset_templates += 1
                tpl = existing_tpl
            else:
                tpl = MessageTemplate.objects.create(
                    tenant=tenant,
                    name=tpl_name,
                    category=tpl_category,
                    subject=tpl_subject,
                    body=tpl_body,
                    is_system=True,
                )
                created_templates += 1

            # 자유양식 템플릿 등 유효한 트리거가 아니면 AutoSendConfig 스킵
            valid_triggers = {c[0] for c in AutoSendConfig.Trigger.choices}
            if trigger not in valid_triggers:
                continue

            existing = existing_configs.get(trigger)
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
                    enabled=True,
                    message_mode="alimtalk",
                    minutes_before=defaults.get("minutes_before"),
                )
                created_configs += 1

        total_configs = AutoSendConfig.objects.filter(tenant=tenant).count()

        # ── 자유양식(freeform_*) 템플릿 자동 검수 신청 ──
        # PFID + API 키가 준비된 경우에만 솔라피에 등록(카카오 검수 대기)
        from django.conf import settings
        import logging as _provision_log
        _plog = _provision_log.getLogger(__name__)
        submitted_reviews = 0
        review_errors = []

        pfid = (tenant.kakao_pfid or "").strip()
        if not pfid:
            default_pf_id = (getattr(settings, "SOLAPI_KAKAO_PF_ID", None) or "").strip()
            pfid = default_pf_id

        if tenant.own_solapi_api_key and tenant.own_solapi_api_secret:
            r_api_key = tenant.own_solapi_api_key
            r_api_secret = tenant.own_solapi_api_secret
        else:
            r_api_key = getattr(settings, "SOLAPI_API_KEY", None) or ""
            r_api_secret = getattr(settings, "SOLAPI_API_SECRET", None) or ""

        can_submit_review = bool(pfid and r_api_key and r_api_secret)
        provider = (tenant.messaging_provider or "solapi").strip().lower()

        if can_submit_review and provider == "solapi":
            freeform_triggers = [k for k in templates.keys() if k.startswith("freeform_")]
            for trigger_key in freeform_triggers:
                tpl_name = templates[trigger_key]["name"]
                tpl_obj = MessageTemplate.objects.filter(tenant=tenant, name=tpl_name).first()
                if not tpl_obj:
                    continue
                # 이미 신청됐고 반려가 아니면 스킵
                if tpl_obj.solapi_template_id and tpl_obj.solapi_status in ("PENDING", "APPROVED"):
                    continue
                try:
                    content = tpl_obj.body.strip()
                    result = create_kakao_template(
                        api_key=r_api_key,
                        api_secret=r_api_secret,
                        channel_id=pfid,
                        name=tpl_obj.name,
                        content=content,
                        category_code="TE",
                    )
                    tpl_obj.solapi_template_id = result.get("templateId", "")
                    tpl_obj.solapi_status = "PENDING"
                    tpl_obj.save(update_fields=["solapi_template_id", "solapi_status", "updated_at"])
                    submitted_reviews += 1
                    _plog.info(
                        "Auto-submitted freeform template for review: tenant=%s name=%s templateId=%s",
                        tenant.id, tpl_obj.name, tpl_obj.solapi_template_id,
                    )
                except (ValueError, Exception) as e:
                    err_msg = f"{tpl_obj.name}: {str(e)[:200]}"
                    review_errors.append(err_msg)
                    _plog.warning("Auto-submit failed: tenant=%s %s", tenant.id, err_msg)

        return Response({
            "created_templates": created_templates,
            "created_configs": created_configs,
            "reset_templates": reset_templates,
            "linked": linked,
            "total_templates": MessageTemplate.objects.filter(tenant=tenant).count(),
            "total_configs": total_configs,
            "submitted_reviews": submitted_reviews,
            "review_errors": review_errors,
            "review_note": (
                "자유양식 템플릿 검수 신청이 완료되었습니다. 카카오 검수는 영업일 1~3일 소요됩니다."
                if submitted_reviews > 0
                else ("PFID 또는 API 키가 미설정이어서 검수 신청을 건너뛰었습니다." if not can_submit_review else "")
            ),
        }, status=status.HTTP_200_OK)
