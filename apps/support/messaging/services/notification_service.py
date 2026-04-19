# apps/support/messaging/services/notification_service.py
"""
이벤트 기반 알림 발송 — send_event_notification, send_clinic_reminder_for_students
"""

import logging
import re

logger = logging.getLogger(__name__)


def send_clinic_reminder_for_students(*args, **kwargs):
    """
    클리닉 리마인더 발송 — 미구현 상태.
    호출 시 not_implemented 상태를 반환하여 프론트엔드에 알림.
    """
    logger.info("send_clinic_reminder_for_students: feature not yet implemented")
    return {
        "status": "not_implemented",
        "message": "클리닉 알림 기능이 아직 준비 중입니다.",
    }


def send_event_notification(
    tenant,
    trigger: str,
    student,
    send_to: str = "parent",  # "parent" | "student"
    context: dict = None,
) -> bool:
    """
    이벤트 기반 자동 알림톡 발송.
    AutoSendConfig에서 enabled 확인 → 템플릿 resolve → enqueue.

    Args:
        tenant: Tenant 인스턴스
        trigger: AutoSendConfig.Trigger 값 (예: "check_in_complete")
        student: Student 인스턴스 (name, phone, parent_phone 필요)
        send_to: "parent" (학부모) 또는 "student"
        context: 추가 치환 변수 dict (예: {"강의명": "수학A반", "차시명": "3차시"})

    Returns:
        bool: enqueue 성공 여부
    """
    from apps.support.messaging.selectors import get_auto_send_config
    from apps.support.messaging.policy import get_owner_tenant_id, is_messaging_disabled, MessagingPolicyError, is_event_dry_run, can_send_sms
    from apps.support.messaging.alimtalk_content_builders import (
        get_solapi_template_id as get_unified_tid,
        get_template_type,
        build_unified_replacements,
    )
    from .queue_service import enqueue_sms
    from .url_helpers import get_tenant_site_url

    if is_messaging_disabled(tenant.id):
        logger.info("send_event_notification skipped: tenant_id=%s messaging disabled", tenant.id)
        return False

    # Dry-run 모드: 로그만 남기고 실발송 안 함
    if is_event_dry_run(trigger):
        student_name = getattr(student, "name", "?")
        logger.info(
            "send_event_notification DRY-RUN: trigger=%s tenant=%s student=%s send_to=%s (not sending)",
            trigger, tenant.id, student_name, send_to,
        )
        return False

    # 1) 현재 테넌트의 config 조회
    config = get_auto_send_config(tenant.id, trigger)
    # 2) 없으면 오너 테넌트 config로 fallback (공용 템플릿 공유)
    if not config:
        owner_id = get_owner_tenant_id()
        if int(tenant.id) != owner_id:
            config = get_auto_send_config(owner_id, trigger)
            if config:
                logger.info(
                    "send_event_notification: owner fallback trigger=%s tenant=%s→owner=%s",
                    trigger, tenant.id, owner_id,
                )
    if not config or not config.enabled:
        logger.debug(
            "send_event_notification skipped: trigger=%s tenant=%s (config not found or disabled)",
            trigger, tenant.id,
        )
        return False

    template = config.template
    if not template:
        logger.debug("send_event_notification skipped: trigger=%s no template linked", trigger)
        return False

    solapi_template_id = (template.solapi_template_id or "").strip()
    solapi_approved = solapi_template_id and template.solapi_status == "APPROVED"

    effective_mode = config.message_mode or "alimtalk"

    # ── 통합 알림톡 템플릿 감지 ──
    # 트리거에 매핑된 통합 템플릿이 있으면 해당 ID 사용
    unified_tid = get_unified_tid(trigger)
    use_unified = bool(unified_tid)

    # 알림톡 미승인 시: 통합 템플릿 > 오너 테넌트 승인 템플릿 폴백
    owner_alimtalk_template_id = ""
    if not solapi_approved and not use_unified:
        # 오너 테넌트에서 같은 트리거의 승인 템플릿 조회
        owner_id = get_owner_tenant_id()
        if int(tenant.id) != owner_id:
            owner_config = get_auto_send_config(owner_id, trigger)
            if owner_config and owner_config.template:
                ot = owner_config.template
                ot_id = (ot.solapi_template_id or "").strip()
                if ot_id and ot.solapi_status == "APPROVED":
                    owner_alimtalk_template_id = ot_id
                    logger.info(
                        "send_event_notification: trigger=%s tenant=%s using owner alimtalk template=%s",
                        trigger, tenant.id, ot_id,
                    )
        if not owner_alimtalk_template_id:
            if effective_mode == "alimtalk":
                logger.debug(
                    "send_event_notification skipped: trigger=%s template not approved (status=%s)",
                    trigger, template.solapi_status,
                )
                return False
            else:
                logger.info(
                    "send_event_notification: trigger=%s alimtalk skipped (not approved, no owner fallback), SMS only",
                    trigger,
                )

    # 수신자 전화번호
    phone = None
    if send_to == "parent":
        phone = (getattr(student, "parent_phone", "") or "").replace("-", "").strip()
    else:
        phone = (getattr(student, "phone", "") or "").replace("-", "").strip()
    if not phone or len(phone) < 10:
        logger.debug(
            "send_event_notification skipped: trigger=%s no valid phone for send_to=%s",
            trigger, send_to,
        )
        return False

    name = (getattr(student, "name", "") or "").strip()
    name_2 = name[-2:] if len(name) >= 2 else name  # 성(첫 글자) 제외 = 이름만
    name_3 = name  # 전체 이름 (하위 호환: 기존 #{학생이름3} 치환)
    academy_name = (getattr(tenant, "name", "") or "").strip()
    site_url = get_tenant_site_url(tenant) or ""

    # ── show_actual_time: ITEM_LIST 시간 필드를 실제 버튼 누른 시각으로 교체 ──
    if context and context.get("_actual_time") and getattr(config, "show_actual_time", False):
        context["시간"] = context["_actual_time"]

    # ── 통합 템플릿 모드: #{선생님메모} + 변수 replacements 빌드 ──
    if use_unified:
        # template.body = #{선생님메모}에 들어갈 안내 문구 (선생님 편집 가능)
        content_body = (template.body or "").strip()

        # Solapi replacements: 선생님메모 + 학원이름 + 학생이름 + 도메인 변수 + 사이트링크
        replacements = build_unified_replacements(
            trigger=trigger,
            content_body=content_body,
            context=context or {},
            tenant_name=academy_name,
            student_name=name,
            site_url=site_url,
        )

        # Solapi text 필드 (알림톡: disable_sms=True → 실제 SMS 발송 안 됨)
        _enqueue_text = next(
            (r["value"] for r in replacements if r["key"] == "선생님메모"),
            content_body,
        )
        _alimtalk_tid = unified_tid

    else:
        # ── 기존 모드 (가입 안내 등 개별 승인 템플릿) ──
        replacements = [
            {"key": "학원명", "value": academy_name},
            {"key": "학생이름", "value": name},
            {"key": "학생이름2", "value": name_2},
            {"key": "학생이름3", "value": name_3},
            {"key": "사이트링크", "value": site_url},
        ]
        for k, v in (context or {}).items():
            if k.startswith("_"):
                continue
            replacements.append({"key": k, "value": str(v)})

        # 오너 폴백 시 #{공지내용} 조합
        if owner_alimtalk_template_id and not any(r["key"] == "공지내용" for r in replacements):
            _parts = []
            for k, v in (context or {}).items():
                if k.startswith("_") or not str(v).strip():
                    continue
                _parts.append(str(v).strip())
            if _parts:
                replacements.append({"key": "공지내용", "value": "\n".join(_parts)})

        # 메시지 본문 (템플릿 치환)
        text = (template.body or "").strip()
        all_vars = {
            "학원명": academy_name, "학생이름": name, "학생이름2": name_2,
            "학생이름3": name_3, "사이트링크": site_url,
        }
        all_vars.update({k: str(v) for k, v in (context or {}).items() if not k.startswith("_")})
        for k, v in all_vars.items():
            text = text.replace(f"#{{{k}}}", v)

        _OPTIONAL_VARS = {"공지내용", "선생님메모", "내용"}
        remaining = re.findall(r"#\{([^}]+)\}", text)
        required_missing = [v for v in remaining if v not in _OPTIONAL_VARS]
        if required_missing:
            logger.error(
                "send_event_notification BLOCKED: trigger=%s template=%s required_vars missing: %s",
                trigger, template.name, required_missing,
            )
            return False
        for opt in _OPTIONAL_VARS:
            text = text.replace(f"#{{{opt}}}", "")
        text = re.sub(r"\n{3,}", "\n\n", text).strip()

        _enqueue_text = text
        _alimtalk_tid = solapi_template_id if solapi_approved else owner_alimtalk_template_id

    sender = (getattr(tenant, "messaging_sender", "") or "").strip()

    # 멱등성 키
    student_id = getattr(student, "id", None) or getattr(student, "pk", None)
    domain_object_id = (context or {}).get("_domain_object_id", "")
    if not domain_object_id:
        from django.utils import timezone as _tz
        domain_object_id = _tz.localtime().strftime("%Y%m%d")
    stable_occurrence = f"{trigger}:{domain_object_id}"

    # ── 발송 모드 결정 ──
    _can_alimtalk = bool(_alimtalk_tid)
    _can_sms = can_send_sms(tenant.id)

    if effective_mode == "both":
        modes_to_send = []
        if _can_alimtalk:
            modes_to_send.append("alimtalk")
        if _can_sms:
            modes_to_send.append("sms")
    elif effective_mode == "alimtalk":
        modes_to_send = ["alimtalk"] if _can_alimtalk else []
    elif effective_mode == "sms":
        if _can_sms:
            modes_to_send = ["sms"]
        else:
            # SMS 불가 시 알림톡으로 바꿔치지 않음 — 채널 정합성 보장
            modes_to_send = []
            logger.info(
                "send_event_notification: trigger=%s tenant=%s SMS unavailable, skipping (no cross-channel fallback)",
                trigger, tenant.id,
            )
    else:
        modes_to_send = []

    if not modes_to_send:
        logger.warning(
            "send_event_notification: trigger=%s tenant=%s no available channel (mode=%s, can_alimtalk=%s, can_sms=%s)",
            trigger, tenant.id, effective_mode, _can_alimtalk, _can_sms,
        )
        return False

    any_success = False
    for mode in modes_to_send:
        try:
            ok = enqueue_sms(
                tenant_id=tenant.id,
                to=phone,
                text=_enqueue_text,
                sender=sender,
                message_mode=mode,
                template_id=_alimtalk_tid if mode == "alimtalk" else None,
                alimtalk_replacements=replacements if mode == "alimtalk" else None,
                event_type=trigger,
                target_type="student",
                target_id=student_id,
                target_name=name,
                occurrence_key=stable_occurrence,
            )
            if ok:
                any_success = True
        except MessagingPolicyError as exc:
            logger.info(
                "send_event_notification policy error: trigger=%s tenant=%s mode=%s reason=%s",
                trigger, tenant.id, mode, exc.reason,
            )
        except Exception as exc:
            logger.exception(
                "send_event_notification failed: trigger=%s tenant=%s mode=%s error=%s",
                trigger, tenant.id, mode, exc,
            )
    return any_success
