# apps/support/messaging/services.py
# SSOT 문서: backend/.claude/domains/messaging.md (수정 시 문서도 동기화)
"""
메시지 발송 서비스 — Solapi(SMS/LMS/알림톡) 연동

- API 키/시크릿: 환경변수 SOLAPI_API_KEY, SOLAPI_API_SECRET (또는 Django 설정)
- 발신번호: SOLAPI_SENDER 또는 settings.SOLAPI_SENDER
"""

import logging
import os
import re
from typing import Optional

from django.conf import settings

logger = logging.getLogger(__name__)


def _get_solapi_credentials() -> tuple[Optional[str], Optional[str]]:
    """Solapi API Key/Secret (환경변수 우선, 설정 fallback). 코드에 키 노출 금지."""
    key = os.environ.get("SOLAPI_API_KEY") or getattr(settings, "SOLAPI_API_KEY", None)
    secret = os.environ.get("SOLAPI_API_SECRET") or getattr(settings, "SOLAPI_API_SECRET", None)
    return (key or None, secret or None)


def _is_mock_mode() -> bool:
    """DEBUG=True 또는 SOLAPI_MOCK=true 이면 실제 API 호출 없이 Mock 사용."""
    if os.environ.get("SOLAPI_MOCK", "").lower() in ("true", "1", "yes"):
        return True
    if getattr(settings, "DEBUG", False):
        return True
    return os.environ.get("DEBUG", "").lower() in ("true", "1", "yes")


def get_solapi_client():
    """
    SolapiMessageService 인스턴스 반환.
    DEBUG=True 또는 SOLAPI_MOCK=true 이면 MockSolapiMessageService (로그만).
    키/시크릿이 없으면 None (스텁 모드).
    """
    if _is_mock_mode():
        from apps.support.messaging.solapi_mock import MockSolapiMessageService
        key, secret = _get_solapi_credentials()
        return MockSolapiMessageService(api_key=key or "", api_secret=secret or "")
    key, secret = _get_solapi_credentials()
    if not key or not secret:
        return None
    try:
        from solapi import SolapiMessageService
        return SolapiMessageService(api_key=key, api_secret=secret)
    except ImportError as e:
        logger.warning("solapi SDK not installed: %s", e)
        return None


def send_sms(
    to: str,
    text: str,
    sender: Optional[str] = None,
    tenant_id: Optional[int] = None,
) -> dict:
    """
    SMS/LMS 즉시 발송 (Solapi).

    Args:
        to: 수신 번호 (01012345678)
        text: 본문
        sender: 발신 번호 (미지정 시 SOLAPI_SENDER 사용)
        tenant_id: 요청 tenant. 지정 시 해당 tenant가 SMS 허용(내 테넌트)인지 검사.

    Returns:
        dict: {"status": "ok"|"error"|"skipped", "group_id"?, "reason"?}
    """
    if tenant_id is not None:
        from apps.support.messaging.policy import can_send_sms, is_messaging_disabled
        if is_messaging_disabled(tenant_id):
            logger.info("send_sms skipped: tenant_id=%s is test tenant (messaging disabled)", tenant_id)
            return {"status": "skipped", "reason": "messaging_disabled_for_test_tenant"}
        if not can_send_sms(tenant_id):
            logger.warning(
                "send_sms blocked by policy: tenant_id=%s is not owner tenant (SMS allowed only for owner)",
                tenant_id,
            )
            return {"status": "error", "reason": "sms_allowed_only_for_owner_tenant"}

    client = get_solapi_client()
    if not client:
        logger.info("send_sms skipped: Solapi not configured")
        return {"status": "skipped", "reason": "solapi_not_configured"}

    sender = (sender or "").strip() or os.environ.get("SOLAPI_SENDER") or getattr(settings, "SOLAPI_SENDER", "")
    if not sender:
        return {"status": "error", "reason": "sender_required"}

    to = (to or "").replace("-", "").strip()
    if not to or not (text or "").strip():
        return {"status": "error", "reason": "to_and_text_required"}

    try:
        from solapi.model import RequestMessage
        message = RequestMessage(from_=sender, to=to, text=text.strip())
        response = client.send(message)
        group_id = getattr(getattr(response, "group_info", None), "group_id", None)
        logger.info("send_sms ok to=%s group_id=%s", to[:4] + "****", group_id)
        return {"status": "ok", "group_id": group_id}
    except Exception as e:
        logger.exception("send_sms failed to=%s", to[:4] + "****")
        return {"status": "error", "reason": str(e)[:500]}


def enqueue_sms(
    tenant_id: int,
    to: str,
    text: str,
    sender: Optional[str] = None,
    *,
    reservation_id: Optional[int] = None,
    message_mode: Optional[str] = None,
    alimtalk_replacements: Optional[list[dict]] = None,
    template_id: Optional[str] = None,
    event_type: Optional[str] = None,
    target_type: Optional[str] = None,
    target_id: Optional[int | str] = None,
    target_name: Optional[str] = None,
    occurrence_key: Optional[str] = None,
) -> bool:
    """
    SMS/알림톡 발송을 SQS에 넣어 워커가 비동기로 발송하도록 함.

    Args:
        tenant_id: 테넌트 ID (워커에서 잔액/PFID 조회)
        to: 수신 번호
        text: 본문
        sender: 발신 번호
        reservation_id: 예약 ID 있으면 워커에서 취소 여부 Double Check 후 발송/스킵
        message_mode: "sms" | "alimtalk"
        alimtalk_replacements: 알림톡 템플릿 치환
        template_id: 알림톡 템플릿 ID (선택)
        event_type: 비즈니스 이벤트 유형 (멱등성 키용, 예: "check_in_complete")
        target_type: 대상 유형 (예: "student")
        target_id: 대상 ID (예: student.id)
        occurrence_key: 이벤트 발생 식별자 (예: "20260328_session_42"). 동일 이벤트 재전송 방지.

    Returns:
        bool: enqueue 성공 여부
    """
    from apps.support.messaging.sqs_queue import MessagingSQSQueue
    from apps.support.messaging.policy import can_send_sms, MessagingPolicyError, is_messaging_disabled, check_recipient_allowed, is_messaging_restricted

    # 로컬 테스트용 tenant(9999): 알림톡·문자 없이 기능만 동작 (발송 스킵)
    if is_messaging_disabled(tenant_id):
        logger.info("enqueue_sms skipped: tenant_id=%s is test tenant (messaging disabled)", tenant_id)
        return False

    # 제한 테넌트: 계정 관련(registration/password) 외 메시징 차단
    # 계정 관련 발송은 OWNER_TENANT_ID로 enqueue되므로 여기서 차단되지 않음
    if is_messaging_restricted(tenant_id):
        logger.info("enqueue_sms blocked: tenant_id=%s messaging restricted (account-only)", tenant_id)
        return False

    # Recipient whitelist guard (테스트 모드 시 허용 번호만 발송)
    if not check_recipient_allowed(to):
        logger.info("enqueue_sms blocked: recipient %s not in test whitelist", (to or "")[:4] + "****")
        return False

    mode = (message_mode or "").strip().lower() or "sms"
    if mode not in ("sms", "alimtalk"):
        mode = "sms"

    # SMS 모드: 자체 키 보유 또는 OWNER 테넌트만 허용
    if mode == "sms":
        if not can_send_sms(tenant_id):
            logger.warning(
                "enqueue_sms blocked by policy: tenant_id=%s cannot send SMS (no own credentials, not owner)",
                tenant_id,
            )
            raise MessagingPolicyError(
                "SMS 발송을 위해서는 자체 발송 계정을 연동하거나 운영자에게 문의하세요.",
                reason="sms_not_allowed",
            )

    queue = MessagingSQSQueue()
    return queue.enqueue(
        tenant_id=tenant_id,
        to=to,
        text=text,
        sender=sender,
        reservation_id=reservation_id,
        message_mode=mode,
        alimtalk_replacements=alimtalk_replacements,
        template_id=template_id,
        event_type=event_type,
        target_type=target_type,
        target_id=target_id,
        target_name=target_name,
        occurrence_key=occurrence_key,
    )


def is_reservation_cancelled(reservation_id: int, tenant_id=None) -> bool:
    """
    예약 취소 여부 (Double Check용).
    tenant_id가 주어지면 해당 테넌트 소속 예약만 조회(격리).
    tenant_id가 없으면 크로스 테넌트 방지를 위해 항상 False 반환.
    """
    if tenant_id is None:
        logger.warning(
            "is_reservation_cancelled called without tenant_id (reservation_id=%s), "
            "returning False to prevent cross-tenant lookup",
            reservation_id,
        )
        return False
    try:
        from django.apps import apps
        for model in apps.get_models():
            if model.__name__ != "Reservation" or not hasattr(model, "status"):
                continue
            if hasattr(model, "tenant_id"):
                r = model.objects.filter(tenant_id=tenant_id, pk=reservation_id).first()
            else:
                # 모델에 tenant_id 필드 없으면 격리 불가 → 안전하게 False
                continue
            if r and getattr(r, "status", None) == "CANCELLED":
                return True
        return False
    except Exception:
        return False


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


def get_site_url(request=None):
    """홈페이지 링크 (메시지용)"""
    from django.conf import settings
    url = getattr(settings, "SITE_URL", None)
    if url:
        return url.rstrip("/")
    if request:
        scheme = "https" if request.is_secure() else "http"
        return f"{scheme}://{request.get_host()}"
    return ""


def get_tenant_site_url(tenant) -> str:
    """
    테넌트별 사이트 URL 반환.
    테넌트의 primary domain이 있으면 https://{host}, 없으면 get_site_url() fallback.
    """
    if tenant is not None:
        try:
            domain = tenant.domains.filter(is_primary=True).first()
            if domain and domain.host:
                return f"https://{domain.host}".rstrip("/")
        except Exception:
            pass
    return get_site_url()


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

        # ── SMS용 text: 사용자 커스텀 template body 직접 치환 (원본 형식 유지) ──
        _sms_vars = {
            "학원명": academy_name, "학원이름": academy_name,
            "학생이름": name, "학생이름2": name_2, "학생이름3": name_3,
            "사이트링크": site_url,
        }
        _sms_vars.update({k: str(v) for k, v in (context or {}).items() if not k.startswith("_")})
        # context 키 → 템플릿 변수명 별칭 매핑 (#{클리닉장소} ← context["장소"] 등)
        _ALIAS = {"장소": "클리닉장소", "클리닉장소": "장소"}
        for src, dst in _ALIAS.items():
            if src in _sms_vars and dst not in _sms_vars:
                _sms_vars[dst] = _sms_vars[src]
        sms_text = content_body
        for k, v in _sms_vars.items():
            sms_text = sms_text.replace(f"#{{{k}}}", v)
        _OPTIONAL_SMS = {"공지내용", "선생님메모", "내용"}
        for opt in _OPTIONAL_SMS:
            sms_text = sms_text.replace(f"#{{{opt}}}", "")
        sms_text = re.sub(r"#\{[^}]+\}", "", sms_text)
        sms_text = re.sub(r"\n{3,}", "\n\n", sms_text).strip()

        # ── 알림톡 SMS 폴백용 text (구조화된 형식) ──
        content_value = next((r["value"] for r in replacements if r["key"] == "선생님메모"), "")
        _ctx = context or {}
        _template_type = get_template_type(trigger)

        def _ctx_val(*keys: str) -> str:
            """context에서 한국어/영어 키 순으로 값 조회."""
            for k in keys:
                v = _ctx.get(k, "")
                if v:
                    return str(v)
            return ""

        def _labeled_lines(pairs: list[tuple[str, str]]) -> str:
            """값이 있는 항목만 '라벨: 값' 줄로 조합."""
            return "\n".join(f"{label}: {val}" for label, val in pairs if val)

        if _template_type == "clinic_info":
            detail_lines = _labeled_lines([
                ("장소", _ctx_val("장소", "place")),
                ("날짜", _ctx_val("날짜", "date")),
                ("시간", _ctx_val("시간", "time")),
            ])
            alimtalk_text = (
                f"{academy_name}입니다.\n\n"
                f"{name}학생님.\n\n"
                f"클리닉 안내 드립니다.\n"
                f"{detail_lines}\n\n"
                f"{content_value}\n"
                f"{site_url}"
            ).strip()
        elif _template_type == "clinic_change":
            detail_lines = _labeled_lines([
                ("기존일정", _ctx_val("클리닉기존일정", "clinic_old_schedule")),
                ("변동사항", _ctx_val("클리닉변동사항", "clinic_changes")),
                ("수정자", _ctx_val("클리닉수정자", "clinic_modifier")),
            ])
            alimtalk_text = (
                f"{academy_name}입니다.\n\n"
                f"{name}학생님. 클리닉 일정이 변경되었습니다.\n\n"
                f"{detail_lines}\n\n"
                f"{content_value}\n"
                f"{site_url}"
            ).strip()
        elif _template_type == "attendance":
            detail_lines = _labeled_lines([
                ("강의", _ctx_val("강의명", "lecture_name")),
                ("차시", _ctx_val("차시명", "session_name")),
                ("반", _ctx_val("반이름", "section_name")),
                ("날짜", _ctx_val("날짜", "date")),
                ("시간", _ctx_val("시간", "time")),
            ])
            alimtalk_text = (
                f"{academy_name}입니다.\n\n"
                f"{name}학생님.\n\n"
                f"출석 안내 드립니다.\n"
                f"{detail_lines}\n\n"
                f"{content_value}\n"
                f"{site_url}"
            ).strip()
        elif _template_type == "score":
            detail_lines = _labeled_lines([
                ("강의", _ctx_val("강의명", "lecture_name")),
                ("차시", _ctx_val("차시명", "session_name")),
            ])
            alimtalk_text = (
                f"{academy_name}입니다.\n\n"
                f"{name}학생님.\n\n"
                f"성적표 안내 드립니다.\n"
                f"{detail_lines}\n\n"
                f"{content_value}\n"
                f"{site_url}"
            ).strip()
        else:
            alimtalk_text = f"{content_value}\n{site_url}".strip()
        alimtalk_text = re.sub(r"\n{3,}", "\n\n", alimtalk_text)

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
        # 통합 모드: SMS는 사용자 커스텀 body, 알림톡은 구조화 텍스트
        if use_unified:
            _text = sms_text if mode == "sms" else alimtalk_text
        else:
            _text = text
        try:
            ok = enqueue_sms(
                tenant_id=tenant.id,
                to=phone,
                text=_text,
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


def send_welcome_messages(
    *,
    created_students: list,
    student_password: str,
    parent_password_by_phone: dict = None,
    site_url: str = "",
):
    """
    가입 안내 알림톡 일괄 발송 (학생 + 학부모).

    셀프가입 승인과 동일한 솔라피 승인 템플릿 사용:
    - 학생: registration_approved_student (#{학생이름}, #{학생아이디}, #{학생비밀번호}, #{사이트링크}, #{비밀번호안내})
    - 학부모: registration_approved_parent (위 + #{학부모아이디}, #{학부모비밀번호})
    """
    parent_password_by_phone = parent_password_by_phone or {}
    sent = 0

    if not created_students:
        return {"status": "skip", "enqueued": 0}

    from apps.support.messaging.policy import MessagingPolicyError, get_owner_tenant_id

    tenant_id = getattr(created_students[0], "tenant_id", None)

    # site_url이 비어 있으면 테넌트의 primary domain에서 자동 파생
    if not site_url:
        tenant_obj = getattr(created_students[0], "tenant", None)
        if tenant_obj is None and tenant_id:
            try:
                from apps.core.models import Tenant
                tenant_obj = Tenant.objects.get(pk=tenant_id)
            except Exception:
                tenant_obj = None
        site_url = get_tenant_site_url(tenant_obj)
    if not tenant_id:
        logger.warning("send_welcome: no tenant_id, skip")
        return {"status": "skip", "enqueued": 0}

    owner_id = get_owner_tenant_id()
    notice = REGISTRATION_APPROVED_NOTICE

    # 셀프가입 승인과 동일한 템플릿 resolve (학생용, 학부모용 각각)
    def _resolve(trigger: str):
        from apps.support.messaging.selectors import get_auto_send_config
        from apps.support.messaging.models import MessageTemplate
        config = get_auto_send_config(owner_id, trigger)
        if config and config.enabled and config.template:
            t = config.template
            sid = (t.solapi_template_id or "").strip()
            if sid and t.solapi_status == "APPROVED":
                return t, sid
        t = MessageTemplate.objects.filter(
            tenant_id=owner_id, category="signup", solapi_status="APPROVED",
        ).exclude(solapi_template_id="").order_by("pk").first()
        if t:
            return t, (t.solapi_template_id or "").strip()
        return None, None

    tmpl_student, sid_student = _resolve("registration_approved_student")
    tmpl_parent, sid_parent = _resolve("registration_approved_parent")

    if not tmpl_student and not tmpl_parent:
        for student in created_students:
            logger.info("send_welcome (stub) student=%s", getattr(student, "name", ""))
        return {"status": "stub", "logged": len(created_students)}

    for student in created_students:
        name = (getattr(student, "name", "") or "").strip()
        ps_number = (getattr(student, "ps_number", "") or "").strip()
        phone = (getattr(student, "phone", "") or "").replace("-", "").strip()
        parent_phone = (getattr(student, "parent_phone", "") or "").replace("-", "").strip()

        # 학생용 — registration_approved_student 템플릿
        if phone and len(phone) >= 10 and tmpl_student and sid_student:
            replacements = {
                "학생이름": name,
                "학생아이디": ps_number,
                "학생비밀번호": student_password,
                "사이트링크": site_url,
                "비밀번호안내": notice,
            }
            body = (tmpl_student.body or "").strip()
            text = body
            for k, v in replacements.items():
                text = text.replace(f"#{{{k}}}", v)
            try:
                ok = enqueue_sms(
                    tenant_id=owner_id,
                    to=phone,
                    text=text,
                    message_mode="alimtalk",
                    template_id=sid_student,
                    alimtalk_replacements=[{"key": k, "value": v} for k, v in replacements.items()],
                )
            except MessagingPolicyError:
                logger.info("send_welcome student skipped (policy: tenant_id=%s)", tenant_id)
                ok = False
            if ok:
                sent += 1

        # 학부모용 — registration_approved_parent 템플릿
        if parent_phone and len(parent_phone) >= 10 and tmpl_parent and sid_parent:
            pwd = parent_password_by_phone.get(parent_phone)
            if not pwd:
                logger.error(
                    "send_welcome_messages SKIP parent: phone=%s no password in mapping, refusing to send with empty/default password",
                    parent_phone[:4] + "****",
                )
                continue
            replacements = {
                "학생이름": name,
                "학생아이디": ps_number,
                "학생비밀번호": student_password,
                "학부모아이디": parent_phone,
                "학부모비밀번호": pwd,
                "사이트링크": site_url,
                "비밀번호안내": notice,
            }
            body = (tmpl_parent.body or "").strip()
            text = body
            for k, v in replacements.items():
                text = text.replace(f"#{{{k}}}", v)
            try:
                ok = enqueue_sms(
                    tenant_id=owner_id,
                    to=parent_phone,
                    text=text,
                    message_mode="alimtalk",
                    template_id=sid_parent,
                    alimtalk_replacements=[{"key": k, "value": v} for k, v in replacements.items()],
                )
            except MessagingPolicyError:
                logger.info("send_welcome parent skipped (policy: tenant_id=%s)", tenant_id)
                ok = False
            if ok:
                sent += 1

    return {"status": "enqueued", "enqueued": sent}


# 가입 승인 알림톡용 플레이스홀더
REGISTRATION_APPROVED_NOTICE = "접속해서 ID·비밀번호를 변경할 수 있습니다."


def send_registration_approved_messages(
    *,
    tenant_id: int,
    site_url: str,
    student_name: str,
    student_phone: str,
    student_id: str,
    student_password: str,
    parent_phone: str,
    parent_password: str,
) -> dict:
    """
    가입 신청 승인 시 학생·학부모에게 알림톡/SMS 발송.

    - 학생용: 트리거 registration_approved_student 템플릿 사용
      플레이스홀더: #{학생이름}, #{학생아이디}, #{학생비밀번호}, #{사이트링크}, #{비밀번호안내}
    - 학부모용: 트리거 registration_approved_parent 템플릿 사용
      플레이스홀더: #{학부모아이디}, #{학부모비밀번호}, #{학생이름}, #{학생아이디}, #{학생비밀번호}, #{사이트링크}, #{비밀번호안내}

    설정이 없거나 비활성화면 발송하지 않음.
    """
    from apps.support.messaging.selectors import get_auto_send_config
    from apps.support.messaging.policy import MessagingPolicyError
    from apps.support.messaging.models import MessageTemplate

    sent = 0
    student_phone = (student_phone or "").replace("-", "").strip()
    parent_phone = (parent_phone or "").replace("-", "").strip()
    site_url = (site_url or "").strip()
    notice = REGISTRATION_APPROVED_NOTICE

    def _resolve_template(trigger: str):
        """오너 테넌트의 승인된 템플릿 사용 (모든 테넌트 공통). SMS fallback 없음."""
        from apps.support.messaging.policy import get_owner_tenant_id
        owner_id = get_owner_tenant_id()
        # 1) 오너 테넌트의 AutoSendConfig
        config = get_auto_send_config(owner_id, trigger)
        if config and config.enabled and config.template:
            t = config.template
            solapi_id = (t.solapi_template_id or "").strip()
            if solapi_id and t.solapi_status == "APPROVED":
                return t, solapi_id, "alimtalk"
        # 2) 오너 테넌트의 승인된 signup 카테고리 템플릿 자동 발견
        t = MessageTemplate.objects.filter(
            tenant_id=owner_id,
            category="signup",
            solapi_status="APPROVED",
        ).exclude(solapi_template_id="").order_by("pk").first()
        if t:
            logger.info(
                "send_registration_approved fallback: trigger=%s using owner template=%s (id=%s)",
                trigger, t.name, t.solapi_template_id,
            )
            return t, (t.solapi_template_id or "").strip(), "alimtalk"
        return None, None, "alimtalk"

    replacements_base = {
        "학생이름": student_name or "",
        "학생아이디": student_id or "",
        "학생비밀번호": student_password or "",
        "사이트링크": site_url,
        "비밀번호안내": notice,
    }

    # 학생용
    if student_phone and len(student_phone) >= 10:
        tmpl, solapi_id, mode = _resolve_template("registration_approved_student")
        if tmpl:
            body = (tmpl.body or "").strip()
            text = body
            for k, v in replacements_base.items():
                text = text.replace(f"#{{{k}}}", v)
            # subject를 text에 합치지 않음 — 카카오 알림톡은 body만 검증, subject 합치면 3034 불일치

            alimtalk_replacements = None
            template_id_solapi = None
            if solapi_id:
                template_id_solapi = solapi_id
                alimtalk_replacements = [{"key": k, "value": v} for k, v in replacements_base.items()]
            try:
                from apps.support.messaging.policy import get_owner_tenant_id as _owner
                if enqueue_sms(
                    tenant_id=_owner(),
                    to=student_phone,
                    text=text,
                    message_mode="alimtalk",
                    template_id=template_id_solapi,
                    alimtalk_replacements=alimtalk_replacements,
                ):
                    sent += 1
            except MessagingPolicyError:
                logger.info("send_registration_approved student skipped (policy: tenant_id=%s)", tenant_id)
        else:
            logger.warning(
                "send_registration_approved student: no template found (tenant_id=%s, trigger=registration_approved_student)",
                tenant_id,
            )

    # 학부모용
    if parent_phone and len(parent_phone) >= 10:
        tmpl, solapi_id, mode = _resolve_template("registration_approved_parent")
        if tmpl:
            parent_id_display = parent_phone
            parent_replacements = {
                **replacements_base,
                "학부모아이디": parent_id_display,
                "학부모비밀번호": parent_password or "",
            }
            body = (tmpl.body or "").strip()
            text = body
            for k, v in parent_replacements.items():
                text = text.replace(f"#{{{k}}}", v)
            # subject를 text에 합치지 않음 — 카카오 알림톡은 body만 검증

            alimtalk_replacements = None
            template_id_solapi = None
            if solapi_id:
                template_id_solapi = solapi_id
                alimtalk_replacements = [{"key": k, "value": v} for k, v in parent_replacements.items()]
            try:
                from apps.support.messaging.policy import get_owner_tenant_id as _owner
                if enqueue_sms(
                    tenant_id=_owner(),
                    to=parent_phone,
                    text=text,
                    message_mode="alimtalk",
                    template_id=template_id_solapi,
                    alimtalk_replacements=alimtalk_replacements,
                ):
                    sent += 1
            except MessagingPolicyError:
                logger.info("send_registration_approved parent skipped (policy: tenant_id=%s)", tenant_id)
        else:
            logger.warning(
                "send_registration_approved parent: no template found (tenant_id=%s, trigger=registration_approved_parent)",
                tenant_id,
            )

    if sent:
        return {"status": "enqueued", "enqueued": sent}
    return {"status": "skip", "enqueued": 0}
