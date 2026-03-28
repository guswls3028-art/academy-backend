# apps/support/messaging/services.py
"""
메시지 발송 서비스 — Solapi(SMS/LMS) 연동

- API 키/시크릿: 환경변수 SOLAPI_API_KEY, SOLAPI_API_SECRET (또는 Django 설정)
- 발신번호: SOLAPI_SENDER 또는 settings.SOLAPI_SENDER
"""

import logging
import os
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
    use_alimtalk_first: bool = False,
    alimtalk_replacements: Optional[list[dict]] = None,
    template_id: Optional[str] = None,
    event_type: Optional[str] = None,
    target_type: Optional[str] = None,
    target_id: Optional[int | str] = None,
    occurrence_key: Optional[str] = None,
) -> bool:
    """
    SMS/알림톡 발송을 SQS에 넣어 워커가 비동기로 발송하도록 함.

    Args:
        tenant_id: 테넌트 ID (워커에서 잔액/PFID 조회)
        to: 수신 번호
        text: 본문 (SMS용 또는 알림톡 실패 시 폴백용)
        sender: 발신 번호
        reservation_id: 예약 ID 있으면 워커에서 취소 여부 Double Check 후 발송/스킵
        message_mode: "sms" | "alimtalk" | "both"
        use_alimtalk_first: (하위호환) True면 both, False면 sms. message_mode가 있으면 무시
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
    from apps.support.messaging.policy import can_send_sms, MessagingPolicyError, is_messaging_disabled, check_recipient_allowed

    # 로컬 테스트용 tenant(9999): 알림톡·문자 없이 기능만 동작 (발송 스킵)
    if is_messaging_disabled(tenant_id):
        logger.info("enqueue_sms skipped: tenant_id=%s is test tenant (messaging disabled)", tenant_id)
        return False

    # Recipient whitelist guard (테스트 모드 시 허용 번호만 발송)
    if not check_recipient_allowed(to):
        logger.info("enqueue_sms blocked: recipient %s not in test whitelist", (to or "")[:4] + "****")
        return False

    mode = (message_mode or "").strip().lower() or None
    if not mode:
        mode = "both" if use_alimtalk_first else "sms"
    if mode not in ("sms", "alimtalk", "both"):
        mode = "sms"

    # SMS 또는 both(알림톡 실패 시 SMS 폴백)인 경우, 자체 키 보유 또는 OWNER 테넌트만 허용
    if mode in ("sms", "both"):
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
        message_mode=message_mode,
        use_alimtalk_first=use_alimtalk_first,
        alimtalk_replacements=alimtalk_replacements,
        template_id=template_id,
        event_type=event_type,
        target_type=target_type,
        target_id=target_id,
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
    from apps.support.messaging.policy import get_owner_tenant_id, is_messaging_disabled, MessagingPolicyError, is_event_dry_run

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
    # 2) 없으면 오너 테넌트 config로 fallback (공용 템플릿 공유 설계)
    if not config:
        owner_id = get_owner_tenant_id()
        if int(tenant.id) != owner_id:
            config = get_auto_send_config(owner_id, trigger)
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
    if not solapi_template_id or template.solapi_status != "APPROVED":
        logger.debug(
            "send_event_notification skipped: trigger=%s template not approved (status=%s)",
            trigger, template.solapi_status,
        )
        return False

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
    name_2 = name[:2] if len(name) >= 2 else name
    academy_name = (getattr(tenant, "name", "") or "").strip()
    site_url = get_tenant_site_url(tenant) or ""

    # 알림톡 치환 변수
    replacements = [
        {"key": "학원명", "value": academy_name},
        {"key": "학생이름", "value": name},
        {"key": "학생이름2", "value": name_2},
        {"key": "사이트링크", "value": site_url},
    ]
    for k, v in (context or {}).items():
        replacements.append({"key": k, "value": str(v)})

    # SMS 본문 (알림톡 실패 시 fallback용)
    text = (template.body or "").strip()
    text = text.replace("#{학원명}", academy_name)
    text = text.replace("#{학생이름}", name)
    text = text.replace("#{학생이름2}", name_2)
    text = text.replace("#{사이트링크}", site_url)
    for k, v in (context or {}).items():
        text = text.replace(f"#{{{k}}}", str(v))

    sender = (getattr(tenant, "messaging_sender", "") or "").strip()

    # 멱등성 키용 메타데이터: trigger + student_id + 오늘 날짜로 동일 이벤트 중복 방지
    student_id = getattr(student, "id", None) or getattr(student, "pk", None)
    from django.utils import timezone as _tz
    stable_occurrence = _tz.localtime().strftime("%Y%m%d")

    try:
        return enqueue_sms(
            tenant_id=tenant.id,
            to=phone,
            text=text,
            sender=sender,
            message_mode=config.message_mode or "alimtalk",
            template_id=solapi_template_id,
            alimtalk_replacements=replacements,
            event_type=trigger,
            target_type="student",
            target_id=student_id,
            occurrence_key=stable_occurrence,
        )
    except MessagingPolicyError as exc:
        logger.info(
            "send_event_notification policy error: trigger=%s tenant=%s reason=%s",
            trigger, tenant.id, exc.reason,
        )
        return False
    except Exception as exc:
        logger.exception(
            "send_event_notification failed: trigger=%s tenant=%s error=%s",
            trigger, tenant.id, exc,
        )
        return False


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
            pwd = parent_password_by_phone.get(parent_phone, "0000")
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
